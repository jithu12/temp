The issue is clear — delete_workspaces_by_owner_account_id catches WorkspaceDeletionFailedError and marks workspaces as “deletion failed” without forcing DELETED status. But since we fixed _delete_workspace to never raise that error, the issue must be coming from delete_workspace (the public method that wraps _delete_workspace).
Check delete_workspace first
Run:

grep -n -A 40 "def delete_workspace\b" dataviz_core/services/workspace.py | head -45


I bet it looks something like this:

def delete_workspace(self, workspace_id, is_failed=False):
    try:
        workspace = self.repositories.workspace.get_by_id(workspace_id)
        ...
        return self._delete_workspace(workspace, is_failed)
    except Exception as e:
        self._update_workspace_with(workspace, status=Status.FAILED)  # ← problem
        raise WorkspaceDeletionFailedError(workspace.id) from e       # ← problem


If delete_workspace has its own try/except that catches exceptions from _delete_workspace and re-raises WorkspaceDeletionFailedError, then fixing _delete_workspace alone isn’t enough — delete_workspace is still catching and re-raising.
Share the output of that grep and I’ll give you the exact fix. But based on the pattern, here’s what the fix will look like:

def delete_workspace(self, workspace_id: uuid.UUID, is_failed: bool = False) -> Workspace:
    workspace = self.repositories.workspace.get_by_id(workspace_id)
    
    try:
        return self._delete_workspace(workspace, is_failed)
    except Exception as e:
        self.logger.exception(
            f"Workspace deletion failed for {workspace_id}. "
            f"Forcing DELETED status. Error: {e}"
        )
        # Force DELETED even if _delete_workspace raised
        return self._update_workspace_with_and_return(
            workspace, status=Status.DELETED
        )


Share the current delete_workspace code and I’ll give you the precise replacement.​​​​​​​​​​​​​​​​
