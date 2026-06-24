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
        self.garbage_collector_service = None
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

    def _assert_not_in_progress(self, account: AccountDetails, owner_account_id: uuid.UUID) -> None:
        """
        Raise AccountNotInActiveException if an operation is already
        in progress on this account. Prevents concurrent modifications.
        """
        if account.status in _IN_PROGRESS_STATUSES:
            self.logger.warning(
                f"Account {owner_account_id} is already in progress. "
                + f"Current status: {account.status}"
            )
            raise AccountNotInActiveException(account.id)

    def get_account_details_by_id(self, workspace: Workspace) -> Account:
        """
        Retrieves account details from the external platform client.
        NOTE: This calls an external API, not the local Dataviz DB.
        For local DB lookup use get_by_owner_id instead.
        """
        self.logger.info("Collecting account information")
        return self.account_client.get_account_by_id(str(workspace.owner_account_id))

    def get_by_owner_id(self, owner_account_id: str) -> AccountDetails:
        """
        Look up AccountDetails by owner_account_id in the local DB.
        Raises AccountNotFoundException if not found.
        """
        filters = [FilteringCriterion("owner_account_id", owner_account_id)]
        results = self.repositories.account_details.list(filters=filters)

        if not results:
            self.logger.error(
                "AccountDetails not found for owner_account_id: " + f"{owner_account_id}"
            )
            raise AccountNotFoundException(
                f"AccountDetails with owner_account_id: {owner_account_id} not found"
            )

        return results[0]

    def get_soft_limit(self, owner_account_id: str):
        """Retrieves the soft limit for the specified owner account."""

        try:
            account_obj: Optional[AccountDetails] = (
                self.repositories.account_details.get_by_owner_account_id(owner_account_id)
            )
            if account_obj:
                return account_obj.soft_limit
        except Exception:
            self.logger.info(
                f"No existing account details found. Creating new for: {owner_account_id}"
            )

        account_details_from_client = self.account_client.get_account_by_id(str(owner_account_id))

        if not account_details_from_client:
            self.logger.warning(
                "Account details not found from client for ID: " + f"{owner_account_id}"
            )
            raise AccountNotFoundException(f"Account details with id: {owner_account_id} not found")

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
        - No active workspaces -> immediately INACTIVE
        - Has active workspaces -> UPDATE_REQUESTED -> async -> INACTIVE
        - On async failure -> FAILED
        """

        if str(account_id) not in ADMIN_ACCOUNTS:
            raise NotOwnerError(account_id, "workspace", owner_account_id)

        try:
            account = self.get_by_owner_id(owner_account_id)
        except Exception:
            self.logger.error(f"Account with id: {owner_account_id} not found")
            raise AccountNotFoundException(f"Account with id: {owner_account_id} not found")

        # Block if already in progress
        self._assert_not_in_progress(account, owner_account_id)

        # Block if already INACTIVE or DELETED

        if account.status is Status.INACTIVE:
            self.logger.info(f"Account {owner_account_id} is already inactive.")
            raise AccountNotInActiveException(account.id)

        if account.status is Status.DELETED:
            self.logger.info(f"Account {owner_account_id} is already deleted.")
            raise AccountNotInActiveException(account.id)

        # Allow ACTIVE and FAILED
        if account.status not in _DEACTIVATABLE_STATUSES:
            self.logger.info(
                f"Account {owner_account_id} cannot be deactivated. "
                + f"Current status: {account.status}"
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
                "No active workspaces found for owner_account_id: " + f"{owner_account_id}"
            )
            return self._update_account_with_and_return(account, status=Status.INACTIVE)

        self.logger.info(f"Requesting {logname(account)} deactivation")

        # Set UPDATE_REQUESTED immediately so status endpoint shows progress
        # deactivate_account (async) will set INACTIVE when all workspaces done
        result = self._update_account_with_and_return(account, status=Status.UPDATE_REQUESTED)

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
            raise AccountNotFoundException(f"Account with id: {owner_account_id} not found")

        self.logger.info("All workspaces deactivation requested for " + f"{owner_account_id}")

        try:
            workspace_details = self.workspace_service.deactivate_workspaces_by_owner_account_id(
                owner_account_id
            )
            self.logger.info(
                f"Deactivation completed for {owner_account_id} | " + f"{workspace_details}"
            )
            return self._update_account_with_and_return(account, status=Status.INACTIVE)

        except Exception as e:
            self.logger.error(f"Deactivation failed for {owner_account_id}. Error: {e}")
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
        - No inactive workspaces -> immediately ACTIVE
        - Has inactive workspaces -> UPDATE_REQUESTED -> async -> ACTIVE
        - On async failure -> FAILED
        """
        if str(account_id) not in ADMIN_ACCOUNTS:
            raise NotOwnerError(account_id, "workspace", owner_account_id)

        try:
            account = self.get_by_owner_id(owner_account_id)
        except Exception:
            raise AccountNotFoundException(f"Account with id: {owner_account_id} not found")

        # Block if already in progress
        self._assert_not_in_progress(account, owner_account_id)

        # Block if already ACTIVE or DELETED
        if account.status is Status.ACTIVE:
            self.logger.info(f"Account {owner_account_id} is already active.")
            raise AccountNotInActiveException(account.id)

        if account.status is Status.DELETED:
            self.logger.info(f"Account {owner_account_id} is already deleted.")
            raise AccountNotInActiveException(account.id)

        # Allow INACTIVE and FAILED
        if account.status not in _REACTIVATABLE_STATUSES:
            self.logger.info(
                f"Account {owner_account_id} cannot be reactivated. "
                + f"Current status: {account.status}"
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
            return self._update_account_with_and_return(account, status=Status.ACTIVE)

        self.logger.info(f"Requesting {logname(account)} reactivation")

        # Set UPDATE_REQUESTED immediately so status endpoint shows progress
        # reactivate_account (async) will set ACTIVE when all workspaces done
        result = self._update_account_with_and_return(account, status=Status.UPDATE_REQUESTED)

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
            raise AccountNotFoundException(f"Account with id: {owner_account_id} not found")

        try:
            workspace_details = self.workspace_service.reactivate_workspaces_by_owner_account_id(
                owner_account_id
            )
            self.logger.info(
                f"Reactivation completed for {owner_account_id} | " + f"{workspace_details}"
            )
            return self._update_account_with_and_return(account, status=Status.ACTIVE)

        except Exception as e:
            self.logger.error(f"Reactivation failed for {owner_account_id}. Error: {e}")
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
        - No pending workspaces -> immediately DELETED
        - Has workspaces -> async -> DELETED
        - On async failure -> FAILED
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
                + "and cannot be deleted."
            )
            raise AccountNotInActiveException(account.id)

        # Fetch ALL non-deleted workspaces
        filters = [FilteringCriterion("owner_account_id", owner_account_id)]
        all_workspaces = self.repositories.workspace.list(filters=filters)
        pending_workspaces = [w for w in all_workspaces if w.status is not Status.DELETED]

        if len(pending_workspaces) == 0:
            self.logger.info(
                "No workspaces to delete for owner_account_id: " + f"{owner_account_id}"
            )
            return self._update_account_with_and_return(account, status=Status.DELETED)

        self.logger.info(
            f"Requesting {logname(account)} deletion - "
            + f"{len(pending_workspaces)} workspace(s) to delete"
        )

        self.workflow_executor.async_exec_core_function(
            service="account",
            function="delete_account",
            kwargs={"owner_account_id": owner_account_id},
        )

        return self._update_account_with_and_return(account, status=Status.DELETED)

    def delete_account(self, owner_account_id: uuid.UUID) -> AccountDetails:
        """
        Async target: deletes all workspaces then marks account DELETED.
        Called by Celery worker. Sets FAILED if anything goes wrong.
        """
        try:
            account = self.get_by_owner_id(owner_account_id)
        except Exception:
            self.logger.error(f"Account with id: {owner_account_id} not found")
            raise AccountNotFoundException(f"Account with id: {owner_account_id} not found")

        try:
            workspace_details = self.workspace_service.delete_workspaces_by_owner_account_id(
                owner_account_id
            )
            self.logger.info(
                f"Deletion completed for {owner_account_id} | " + f"{workspace_details}"
            )
            return self._update_account_with_and_return(account, status=Status.DELETED)

        except Exception as e:
            self.logger.error(f"Deletion failed for {owner_account_id}. Error: {e}")
            self._update_account_with(account, status=Status.FAILED)
            raise

    def set_garbage_collector_service(self, garbage_collector_service):
        """Inject garbage collector service for grace period tracking."""
        self.garbage_collector_service = garbage_collector_service

    def set_workflow_executor(self, workflow_executor):
        """Inject workflow executor for async Celery tasks."""
        self.workflow_executor = workflow_executor

    # -------------------------------------------------------------------------
    # Lifecycle Event Handlers (moved from AccountLifecycleConsumer)
    # -------------------------------------------------------------------------

    def handle_resource_disabled(
        self,
        account_id: uuid.UUID,
        event_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Handle LifecycleEvent.ResourceDisabled from Accounts Team.

        When Accounts Team deactivates an account (starts grace period),
        they publish this event. We:
        1. Set deletion date via garbage collector (for grace period tracking)
        2. Trigger async deactivation of all active workspaces
        3. Update account status to INACTIVE

        Args:
            account_id: The account UUID
            event_data: Optional additional data from the event
        """
        try:
            self.logger.info(
                "AccountService: Received ResourceDisabled event for account "
                + f"{account_id} (grace period starts)"
            )

            try:
                account = self.get_by_owner_id(account_id)
            except AccountNotFoundException:
                self.logger.warning(
                    f"AccountService: Account {account_id} not found. "
                    + "Skipping ResourceDisabled event."
                )
                return

            if account.status == Status.INACTIVE:
                self.logger.info(
                    f"AccountService: Account {account_id} already INACTIVE. No action needed."
                )
                return

            if account.status == Status.DELETED:
                self.logger.info(f"AccountService: Account {account_id} already DELETED. Skipping.")
                return

            filters = [
                FilteringCriterion("owner_account_id", account_id),
                FilteringCriterion("status", Status.ACTIVE),
            ]
            active_workspaces = self.repositories.workspace.list(filters=filters)

            if len(active_workspaces) == 0:
                self.logger.info(
                    f"AccountService: No active workspaces for {account_id}. "
                    + "Setting INACTIVE immediately."
                )
                self._update_account_with(account, status=Status.INACTIVE)
                self.logger.info(f"AccountService: Account {account_id} set to INACTIVE")
                return

            if self.garbage_collector_service is not None:
                grace_period_days = getattr(account, "grace_period_days", 30)
                self.logger.info(
                    f"AccountService: Setting deletion date for {account_id} "
                    + f"(grace period: {grace_period_days} days)"
                )
                self.garbage_collector_service.set_deletion_date_for_account(
                    account=account,
                    grace_period_days=grace_period_days,
                )
            else:
                self.logger.warning(
                    "AccountService: garbage_collector_service not set - "
                    + f"skipping deletion date for {account_id}"
                )

            self._update_account_with(account, status=Status.UPDATE_REQUESTED)

            if self.workflow_executor is not None:
                self.logger.info(
                    "AccountService: Triggering async deactivation of "
                    + f"{len(active_workspaces)} workspace(s) for {account_id}"
                    )
                self.workflow_executor.async_exec_core_function(
                    service="account",
                    function="deactivate_account",
                    kwargs={"owner_account_id": account_id},
                )
            else:
                self.logger.error(
                    "AccountService: workflow_executor not set - cannot trigger async "
                    + f"deactivation for {account_id}"
                )

            self.logger.info(
                f"AccountService: Account {account_id} deactivation "
                + "started (status: UPDATE_REQUESTED -> async task will set INACTIVE)"
            )

        except Exception as e:
            self.logger.error(
                f"AccountService: Failed to handle ResourceDisabled for account {account_id}: {e}",
                exc_info=True,
            )
            raise

    def handle_resource_active(
        self,
        account_id: uuid.UUID,
        event_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Handle LifecycleEvent.ResourceActive from Accounts Team.

        When Accounts Team reactivates an account during grace period,
        they publish this event. We:
        1. Cancel the deletion date (grace period cancelled)
        2. Trigger async reactivation of all inactive workspaces
        3. Update account status to ACTIVE

        Args:
            account_id: The account UUID
            event_data: Optional additional data from the event
        """
        try:
            self.logger.info(
                "AccountService: Received ResourceActive event for account "
                + f"{account_id} (reactivation, grace period cancelled)"
            )

            try:
                account = self.get_by_owner_id(account_id)
            except AccountNotFoundException:
                self.logger.warning(
                    f"AccountService: Account {account_id} not found. "
                    + "Skipping ResourceActive event."
                )
                return

            if account.status == Status.ACTIVE:
                self.logger.info(
                    f"AccountService: Account {account_id} " + "already ACTIVE. No action needed."
                )
                return

            if account.status == Status.DELETED:
                self.logger.warning(
                    f"AccountService: Account {account_id} " + "is DELETED. Cannot reactivate."
                )
                return

            filters = [
                FilteringCriterion("owner_account_id", account_id),
                FilteringCriterion("status", Status.INACTIVE),
            ]
            inactive_workspaces = self.repositories.workspace.list(filters=filters)

            if len(inactive_workspaces) == 0:
                self.logger.info(
                    f"AccountService: No inactive workspaces for {account_id}. "
                    + "Setting ACTIVE immediately."
                )
                self._update_account_with(account, status=Status.ACTIVE)
                self.logger.info(f"AccountService: Account {account_id} set to ACTIVE")
                return

            self._update_account_with(account, status=Status.UPDATE_REQUESTED)

            if self.workflow_executor is not None:
                self.logger.info(
                    "AccountService: Triggering async reactivation of "
                    + f"{len(inactive_workspaces)} workspace(s) for {account_id}"
                )
                self.workflow_executor.async_exec_core_function(
                    service="account",
                    function="reactivate_account",
                    kwargs={"owner_account_id": account_id},
                )
            else:
                self.logger.error(
                    "AccountService: workflow_executor not set - cannot trigger async "
                    + f"reactivation for {account_id}"
                )

            self.logger.info(
                f"AccountService: Account {account_id} reactivation started "
                + "(status: UPDATE_REQUESTED -> async task will set ACTIVE)"
            )

        except Exception as e:
            self.logger.error(
                f"AccountService: Failed to handle ResourceActive for account {account_id}: {e}",
                exc_info=True,
            )
            raise

    def handle_resource_deleting(
        self,
        account_id: uuid.UUID,
        event_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Handle LifecycleEvent.ResourceDeleting from Accounts Team.

        When Accounts Team permanently deletes an account (after grace period),
        they publish this event. We:
        1. Trigger async deletion of all workspaces
        2. Update account status to DELETED

        Args:
            account_id: The account UUID
            event_data: Optional additional data from the event
        """
        try:
            self.logger.info(
                "AccountService: Received ResourceDeleting event for account "
                + f"{account_id} (pending deletion)"
            )

            account = self.repositories.account_details.get(owner_account_id=account_id)

            if not account:
                self.logger.warning(
                    f"AccountService: Account {account_id} not found. "
                    + "Skipping ResourceDeleting event."
                )
                return

            if account.status == Status.DELETED:
                self.logger.info(
                    f"AccountService: Account {account_id} " + "already DELETED. No action needed."
                )
                return

            filters = [FilteringCriterion("owner_account_id", account_id)]
            all_workspaces = self.repositories.workspace.list(filters=filters)
            pending_workspaces = [w for w in all_workspaces if w.status != Status.DELETED]

            if len(pending_workspaces) == 0:
                self.logger.info(
                    f"AccountService: No workspaces to delete for {account_id}. "
                    + "Marking account as DELETED."
                )
                with self.autocommit():
                    self.repositories.account_details.update(
                        id=account.id,
                        status=Status.DELETED,
                    )
                self.logger.info(f"AccountService: Account {account_id} set to DELETED")
                return

            if self.workflow_executor is not None:
                self.logger.info("AccountService: Triggering async deletion of "
                    + f"{len(pending_workspaces)} workspace(s) for {account_id}"
                )
                self.workflow_executor.async_exec_core_function(
                    service="account",
                    function="delete_account",
                    kwargs={"owner_account_id": account_id},
                )
                with self.autocommit():
                    self.repositories.account_details.update(
                        id=account.id,
                        status=Status.DELETED,
                    )
                return

            if self.workspace_service is not None:
                self.logger.info(
                    "AccountService: Using workspace_service to delete "
                    + f"{len(pending_workspaces)} workspace(s) for {account_id}"
                )
                self.workspace_service.delete_workspaces_by_owner_account_id(
                    account.owner_account_id
                )
                with self.autocommit():
                    self.repositories.account_details.update(
                        id=account.id,
                        status=Status.DELETED,
                    )
                return

            raise RuntimeError(
                f"AccountService: Cannot delete workspaces for {account_id} - "
                + "no workflow_executor or workspace_service available"
            )

        except Exception as e:
            self.logger.error(
                f"AccountService: Failed to handle ResourceDeleting for account {account_id}: {e}",
                exc_info=True,
            )
            raise

    def handle_resource_deleted(
        self,
        account_id: uuid.UUID,
        event_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Handle LifecycleEvent.ResourceDeleted from Accounts Team.

        When Accounts Team confirms account deletion is complete,
        they publish this event. We:
        1. Verify all workspaces are deleted
        2. If resources remain, send notification email to Dataviz team

        Args:
            account_id: The account UUID
            event_data: Optional additional data from the event
        """
        try:
            self.logger.info(
                "AccountService: Received ResourceDeleted event for account "
                + f"{account_id} (deletion verification)"
            )

            account = self.repositories.account_details.get(owner_account_id=account_id)

            if not account:
                self.logger.warning(
                    f"AccountService: Account {account_id} not found in DB. "
                    + "Skipping ResourceDeleted event."
                )
                return

            # Check for remaining resources
            filters = [FilteringCriterion("owner_account_id", account_id)]
            all_workspaces = self.repositories.workspace.list(filters=filters)
            remaining_workspaces = [w for w in all_workspaces if w.status != Status.DELETED]

            if len(remaining_workspaces) == 0:
                self.logger.info(
                    f"AccountService: Account {account_id} deletion verified - "
                    + "no remaining resources."
                )
                return

            # Resources still exist - send notification to Dataviz team
            self.logger.error(
                f"AccountService: Account {account_id} marked DELETED but "
                + f"{len(remaining_workspaces)} workspace(s) remain: "
                + f"{[w.id for w in remaining_workspaces]}"
            )

            # Compose notification email
            workspace_details = "\n".join(
                [
                    f"  - Workspace ID: {w.id}, Name: {w.name}, Status: {w.status}"
                    for w in remaining_workspaces
                ]
            )

            email_subject = f"Manual Cleanup Required: Account {account_id}"
            email_body = (
                f"Account {account_id} has been marked as DELETED by Accounts Team, "
                + f"but {len(remaining_workspaces)} workspace(s) could not be deleted "
                + "automatically.\n\n"
                + "Remaining workspaces:\n"
                + workspace_details
                + "\n\nPlease investigate and manually clean up these resources."
            )

            # Send notification if monitoring service available
            if hasattr(self, "monitoring_service") and self.monitoring_service is not None:
                try:
                    self.monitoring_service.send_notification(
                        error_level="ERROR",
                        email_subject=email_subject,
                        email_body=email_body,
                    )
                    self.logger.info(
                        f"AccountService: Sent notification email for account {account_id}"
                    )
                except Exception as e:
                    self.logger.error(
                        f"AccountService: Failed to send notification email: {e}",
                        exc_info=True,
                    )
            else:
                self.logger.warning(
                    "AccountService: monitoring_service not available - "
                    + f"cannot send notification for {account_id}"
                )

        except Exception as e:
            self.logger.error(
                f"AccountService: Failed to handle ResourceDeleted for account {account_id}: {e}",
                exc_info=True,
            )
            raise
    def _refresh_workspace(self, workspace_id: uuid.UUID) -> Workspace:
        workspace = self.repositories.workspace.get_by_id(workspace_id)
        self.logger.info(f"Refreshing {logname(workspace)}...")

        if workspace.status in [
            Status.CREATION_REQUESTED,
            Status.DELETION_REQUESTED,
            Status.DELETED,
        ]:
            self.logger.info(
                f"{logname(workspace)} status not changed: {workspace.status}"
            )
            return workspace

        if workspace.dns is not None:
            self._dns._refresh_dns(workspace.dns)
        else:
            raise NotFoundError("Workspace", "DNS")

        if workspace.dataplane_component is not None:
            self._dataplane._refresh_component(workspace.dataplane_component)
        else:
            raise NotFoundError("Workspace", "Dataplane")

        # if workspace.kube_stack is not None:
        #     self._kube_service._refresh_stack(workspace.kube_stack)

        flatten_status = set(
            [
                workspace.dns.status,
                workspace.dataplane_component.status,
                # workspace.kube_stack.status,
            ]
        )

        return self._update_workspace_with_and_return(
            workspace,
            status=self.status_from_dependencies(
                Status(workspace.status),
                flatten_status,
            ),  # FIXME: typing issue here
        )

    def handle_event(
        self,
        event_type: str,
        account_id: uuid.UUID,
        event_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Route incoming account lifecycle events to handler methods.

        Args:
            event_type: The type of lifecycle event
            account_id: The account UUID
            event_data: Optional additional data from the event
        """
        handlers = {
            "LifecycleEvent.ResourceDisabled": self.handle_resource_disabled,
            "ResourceDisabled": self.handle_resource_disabled,
            "LifecycleEvent.ResourceActive": self.handle_resource_active,
            "ResourceActive": self.handle_resource_active,
            "LifecycleEvent.ResourceDeleting": self.handle_resource_deleting,
            "ResourceDeleting": self.handle_resource_deleting,
            "LifecycleEvent.ResourceDeleted": self.handle_resource_deleted,
            "ResourceDeleted": self.handle_resource_deleted,
        }

        handler = handlers.get(event_type)
        if handler is None:
            self.logger.warning(
                f"AccountService: Unknown event type '{event_type}' for "
                + f"account {account_id}. Ignoring."
            )
            return

        handler(account_id, event_data)
