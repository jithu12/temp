Here it is:

```python
def _update_account_with(
    self,
    account: AccountDetails,
    **changes: Any
) -> Dict[str, Any]:

    actual_changes = update_resource_with(
        ctx_manager=self.autocommit(),
        repository=self.repositories.account_details,
        resource=account,
        logger=self.logger,
        **changes,
    )

    return actual_changes
```

That's the complete function. Only one word changed from what you had — `repositories=` became `repository=`. Everything else stays exactly the same.
