import logging
import uuid
from typing import Callable, Optional, Union, Any, Dict

from dataviz_core.adapters.account_client import AccountClient, Account
from dataviz_core.utils import logname, update_resource_with
from dataviz_core.models import Status
from dataviz_core.models.workspace import Workspace
from dataviz_core.models.account_details import AccountDetails
from dataviz_core.services.interfaces import SessionProvider
from dataviz_core.services.session import SessionManagerMixin
from dataviz_core.repositories.context import RepositoryContext
from dataviz_core.utils.logging import get_default_logger
from dataviz_core.config.const import WORKSPACE_LIMIT_PER_ACCOUNT, ADMIN_ACCOUNTS
from dataviz_core.services.filtering import FilteringCriterion
from dataviz_core.errors.exceptions import (
    AccountNotFoundException,
    NotOwnerError,
    AccountNotInActiveException,
)

LoggerType = Union[logging.Logger, logging.LoggerAdapter]

# Statuses that mean an operation is already in progress
_IN_PROGRESS_STATUSES = (Status.UPDATE_REQUESTED, Status.UPDATING)

# Statuses from which deactivation is allowed
_DEACTIVATABLE_STATUSES = (Status.ACTIVE, Status.FAILED)

# Statuses from which reactivation is allowed
_REACTIVATABLE_STATUSES = (Status.INACTIVE, Status.FAILED)

# Statuses from which deletion is allowed
_DELETABLE_STATUSES = (Status.ACTIVE, Status.INACTIVE, Status.FAILED)


class AccountService(SessionManagerMixin):

    def __init__(
        self,
        account_client: AccountClient,
        session_provider: SessionProvider,
        repository_context: Optional[RepositoryContext] = None,
        is_retrying_func: Callable[[], bool] = lambda: False,
        logger: Optional[LoggerType] = None,
        workflow_executor=None,
    ) -> None:

        super().__init__(
            session_provider,
            repository_context=repository_context,
        )

        self.is_retrying_func = is_retrying_func
        self.account_client = account_client
        self.logger = logger if logger else get_default_logger(__name__)
        self.workspace_service = None
        self.workflow_executor = workflow_executor

    def set_workspace_service(self, workspace_service):
        """Inject workspace service to avoid circular dependency."""
        self.workspace_service = workspace_service

    def _update_account_with_and_return(
        self,
        account: AccountDetails,
        **changes: Any,
    ) -> AccountDetails:
        if self._update_account_with(account, **changes):
            return self.repositories.account_details.get_by_id(account.id)
        return account

    def _update_account_with(
        self,
        account: AccountDetails,
        **changes: Any,
    ) -> Dict[str, Any]:
        return update_resource_with(
            ctx_manager=self.autocommit(),
            repository=self.repositories.account_details,
            resource=account,
            logger=self.logger,
            **changes,
        )

    def _assert_not_in_progress(
        self, account: AccountDetails, owner_account_id: uuid.UUID
    ) -> None:
        """
        Raise AccountNotInActiveException if an operation is already
        in progress on this account. Prevents concurrent modifications.
        """
        if account.status in _IN_PROGRESS_STATUSES:
            self.logger.warning(
                f"Account {owner_account_id} is already in progress. "
                f"Current status: {account.status}"
            )
            raise AccountNotInActiveException(account.id)

    def get_account_details_by_id(self, workspace: Workspace) -> Account:
        """
        Retrieves account details from the external platform client.
        NOTE: This calls an external API, not the local Dataviz DB.
        For local DB lookup use get_by_owner_id instead.
        """
        self.logger.info("Collecting account information")
        return self.account_client.get_account_by_id(
            str(workspace.owner_account_id)
        )

    def get_by_owner_id(self, owner_account_id: str) -> AccountDetails:
        """
        Look up AccountDetails by owner_account_id in the local DB.
        Raises AccountNotFoundException if not found.
        """
        filters = [FilteringCriterion("owner_account_id", owner_account_id)]
        results = self.repositories.account_details.list(filters=filters)

        if not results:
            self.logger.error(
                f"AccountDetails not found for owner_account_id: {owner_account_id}"
            )
            raise AccountNotFoundException(
                f"AccountDetails with owner_account_id: {owner_account_id} not found"
            )

        return results[0]

    def get_soft_limit(self, owner_account_id: str):
        """Retrieves the soft limit for the specified owner account."""

        try:
            account_obj: Optional[AccountDetails] = (
                self.repositories.account_details.get_by_owner_account_id(
                    owner_account_id
                )
            )
            if account_obj:
                return account_obj.soft_limit
        except Exception:
            self.logger.info(
                f"No existing account details found. Creating new for: {owner_account_id}"
            )

        account_details_from_client = self.account_client.get_account_by_id(
            str(owner_account_id)
        )

        if not account_details_from_client:
            self.logger.warning(
                f"Account details not found from client for ID: {owner_account_id}"
            )
            raise Exception(
                f"Error while getting Account details with id: {owner_account_id}"
            )

        account_details = AccountDetails(
            name=account_details_from_client.name,
            owner_account_id=account_details_from_client.id,
            soft_limit=WORKSPACE_LIMIT_PER_ACCOUNT,
        )

        if self.autocommit():
            self.repositories.account_details.insert(account_details)

        return account_details.soft_limit

    def request_account_deactivation(
        self,
        owner_account_id: uuid.UUID,
        account_id: uuid.UUID,
    ) -> AccountDetails:
        """
        Request deactivation of an account and all its active workspaces.

        Allowed from: ACTIVE, FAILED (retry after failed deactivation)
        Blocked if:   INACTIVE, DELETED, UPDATE_REQUESTED (in progress)

        Flow:
        - No active workspaces → immediately INACTIVE
        - Has active workspaces → UPDATE_REQUESTED → async → INACTIVE
        - On async failure → FAILED
        """
        if str(account_id) not in ADMIN_ACCOUNTS:
            raise NotOwnerError(account_id, "workspace", owner_account_id)

        try:
            account = self.get_by_owner_id(owner_account_id)
        except Exception:
            self.logger.error(f"Account with id: {owner_account_id} not found")
            raise AccountNotFoundException(
                f"Account with id: {owner_account_id} not found"
            )

        # Block if already in progress
        self._assert_not_in_progress(account, owner_account_id)

        # Block if already INACTIVE or DELETED
        if account.status is Status.INACTIVE:
            self.logger.info(
                f"Account {owner_account_id} is already inactive."
            )
            raise AccountNotInActiveException(account.id)

        if account.status is Status.DELETED:
            self.logger.info(
                f"Account {owner_account_id} is already deleted."
            )
            raise AccountNotInActiveException(account.id)

        # Allow ACTIVE and FAILED
        if account.status not in _DEACTIVATABLE_STATUSES:
            self.logger.info(
                f"Account {owner_account_id} cannot be deactivated. "
                f"Current status: {account.status}"
            )
            raise AccountNotInActiveException(account.id)

        # Only look for ACTIVE workspaces for deactivation
        filters = [
            FilteringCriterion("owner_account_id", owner_account_id),
            FilteringCriterion("status", Status.ACTIVE),
        ]
        workspaces = self.repositories.workspace.list(filters=filters)

        if len(workspaces) == 0:
            self.logger.info(
                f"No active workspaces found for owner_account_id: {owner_account_id}"
            )
            return self._update_account_with_and_return(
                account, status=Status.INACTIVE
            )

        self.logger.info(f"Requesting {logname(account)} deactivation")

        # Set UPDATE_REQUESTED immediately so status endpoint shows progress
        # deactivate_account (async) will set INACTIVE when all workspaces done
        result = self._update_account_with_and_return(
            account, status=Status.UPDATE_REQUESTED
        )

        self.workflow_executor.async_exec_core_function(
            service="account",
            function="deactivate_account",
            kwargs={"owner_account_id": owner_account_id},
        )

        return result

    def deactivate_account(self, owner_account_id: uuid.UUID) -> AccountDetails:
        """
        Async target: deactivates all workspaces then marks account INACTIVE.
        Called by Celery worker. Sets FAILED if anything goes wrong.
        """
        try:
            account = self.get_by_owner_id(owner_account_id)
        except Exception:
            self.logger.error(f"Account with id: {owner_account_id} not found")
            raise AccountNotFoundException(
                f"Account with id: {owner_account_id} not found"
            )

        self.logger.info(
            f"All workspaces deactivation requested for {owner_account_id}"
        )

        try:
            workspace_details = (
                self.workspace_service.deactivate_workspaces_by_owner_account_id(
                    owner_account_id
                )
            )
            self.logger.info(
                f"Deactivation completed for {owner_account_id} | {workspace_details}"
            )
            return self._update_account_with_and_return(
                account, status=Status.INACTIVE
            )

        except Exception as e:
            self.logger.error(
                f"Deactivation failed for {owner_account_id}. Error: {e}"
            )
            self._update_account_with(account, status=Status.FAILED)
            raise

    def request_account_reactivation(
        self,
        owner_account_id: uuid.UUID,
        account_id: uuid.UUID,
    ) -> AccountDetails:
        """
        Request reactivation of an account and all its inactive workspaces.

        Allowed from: INACTIVE, FAILED (retry after failed reactivation)
        Blocked if:   ACTIVE, DELETED, UPDATE_REQUESTED (in progress)

        Flow:
        - No inactive workspaces → immediately ACTIVE
        - Has inactive workspaces → UPDATE_REQUESTED → async → ACTIVE
        - On async failure → FAILED
        """
        if str(account_id) not in ADMIN_ACCOUNTS:
            raise NotOwnerError(account_id, "workspace", owner_account_id)

        try:
            account = self.get_by_owner_id(owner_account_id)
        except Exception:
            raise AccountNotFoundException(
                f"Account with id: {owner_account_id} not found"
            )

        # Block if already in progress
        self._assert_not_in_progress(account, owner_account_id)

        # Block if already ACTIVE or DELETED
        if account.status is Status.ACTIVE:
            self.logger.info(
                f"Account {owner_account_id} is already active."
            )
            raise AccountNotInActiveException(account.id)

        if account.status is Status.DELETED:
            self.logger.info(
                f"Account {owner_account_id} is already deleted."
            )
            raise AccountNotInActiveException(account.id)

        # Allow INACTIVE and FAILED
        if account.status not in _REACTIVATABLE_STATUSES:
            self.logger.info(
                f"Account {owner_account_id} cannot be reactivated. "
                f"Current status: {account.status}"
            )
            raise AccountNotInActiveException(account.id)

        # Only look for INACTIVE workspaces for reactivation
        filters = [
            FilteringCriterion("owner_account_id", owner_account_id),
            FilteringCriterion("status", Status.INACTIVE),
        ]
        workspaces = self.repositories.workspace.list(filters=filters)

        if len(workspaces) == 0:
            self.logger.info(
                f"No inactive workspaces found for owner_account_id: {owner_account_id}"
            )
            return self._update_account_with_and_return(
                account, status=Status.ACTIVE
            )

        self.logger.info(f"Requesting {logname(account)} reactivation")

        # Set UPDATE_REQUESTED immediately so status endpoint shows progress
        # reactivate_account (async) will set ACTIVE when all workspaces done
        result = self._update_account_with_and_return(
            account, status=Status.UPDATE_REQUESTED
        )

        self.workflow_executor.async_exec_core_function(
            service="account",
            function="reactivate_account",
            kwargs={"owner_account_id": owner_account_id},
        )

        return result

    def reactivate_account(self, owner_account_id: uuid.UUID) -> AccountDetails:
        """
        Async target: reactivates all workspaces then marks account ACTIVE.
        Called by Celery worker. Sets FAILED if anything goes wrong.
        """
        try:
            account = self.get_by_owner_id(owner_account_id)
        except Exception:
            self.logger.error(f"Account with id: {owner_account_id} not found")
            raise AccountNotFoundException(
                f"Account with id: {owner_account_id} not found"
            )

        try:
            workspace_details = (
                self.workspace_service.reactivate_workspaces_by_owner_account_id(
                    owner_account_id
                )
            )
            self.logger.info(
                f"Reactivation completed for {owner_account_id} | {workspace_details}"
            )
            return self._update_account_with_and_return(
                account, status=Status.ACTIVE
            )

        except Exception as e:
            self.logger.error(
                f"Reactivation failed for {owner_account_id}. Error: {e}"
            )
            self._update_account_with(account, status=Status.FAILED)
            raise

    def request_account_deletion(
        self,
        owner_account_id: uuid.UUID,
        account_id: uuid.UUID,
    ) -> AccountDetails:
        """
        Request deletion of an account and ALL its workspaces.

        Allowed from: ACTIVE, INACTIVE, FAILED
        Blocked if:   DELETED, UPDATE_REQUESTED (in progress)

        Flow:
        - No pending workspaces → immediately DELETED
        - Has workspaces → async → DELETED
        - On async failure → FAILED
        """
        if str(account_id) not in ADMIN_ACCOUNTS:
            raise NotOwnerError(account_id, "workspace", owner_account_id)

        account = self.get_by_owner_id(owner_account_id)

        # Block if already in progress
        self._assert_not_in_progress(account, owner_account_id)

        # Block if already DELETED
        if account.status is Status.DELETED:
            self.logger.info(f"Account {owner_account_id} is already deleted.")
            raise AccountNotInActiveException(account.id)

        # Allow ACTIVE, INACTIVE, FAILED
        if account.status not in _DELETABLE_STATUSES:
            self.logger.info(
                f"Account {owner_account_id} is in state {account.status} "
                f"and cannot be deleted."
            )
            raise AccountNotInActiveException(account.id)

        # Fetch ALL non-deleted workspaces
        filters = [FilteringCriterion("owner_account_id", owner_account_id)]
        all_workspaces = self.repositories.workspace.list(filters=filters)
        pending_workspaces = [
            w for w in all_workspaces if w.status is not Status.DELETED
        ]

        if len(pending_workspaces) == 0:
            self.logger.info(
                f"No workspaces to delete for owner_account_id: {owner_account_id}"
            )
            return self._update_account_with_and_return(
                account, status=Status.DELETED
            )

        self.logger.info(
            f"Requesting {logname(account)} deletion — "
            f"{len(pending_workspaces)} workspace(s) to delete"
        )

        self.workflow_executor.async_exec_core_function(
            service="account",
            function="delete_account",
            kwargs={"owner_account_id": owner_account_id},
        )

        return self._update_account_with_and_return(
            account, status=Status.DELETED
        )

    def delete_account(self, owner_account_id: uuid.UUID) -> AccountDetails:
        """
        Async target: deletes all workspaces then marks account DELETED.
        Called by Celery worker. Sets FAILED if anything goes wrong.
        """
        try:
            account = self.get_by_owner_id(owner_account_id)
        except Exception:
            self.logger.error(f"Account with id: {owner_account_id} not found")
            raise AccountNotFoundException(
                f"Account with id: {owner_account_id} not found"
            )

        try:
            workspace_details = (
                self.workspace_service.delete_workspaces_by_owner_account_id(
                    owner_account_id
                )
            )
            self.logger.info(
                f"Deletion completed for {owner_account_id} | {workspace_details}"
            )
            return self._update_account_with_and_return(
                account, status=Status.DELETED
            )

        except Exception as e:
            self.logger.error(
                f"Deletion failed for {owner_account_id}. Error: {e}"
            )
            self._update_account_with(account, status=Status.FAILED)
            raise
