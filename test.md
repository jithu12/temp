Good — `SimpleNamespace` IS applied. But the test is still failing because the `_delete_workspace` code is checking `if workspace.sg_connect is not None` but something else is going wrong.

Run this to see what your `_delete_workspace` currently looks like:

```bash
grep -n "sg_connect" dataviz_core/services/workspace.py
```

Share the output. I need to see if the `if workspace.sg_connect is not None:` guard is actually in the code or not. Because if the guard isn't there, it'll call `remove_redirect_url` regardless of whether `sg_connect` is `None`.

Also run:
```bash
sed -n '930,960p' dataviz_core/services/workspace.py
```

That'll show the actual `_deactivate_workspace` or `_delete_workspace` function body where the sg_connect call happens. Share both outputs.
