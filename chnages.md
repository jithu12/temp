You can explain it like this in simple English during the meeting:

---

### First Error

```text
FAILED ... StopIteration
```

You can say:

> "The first error happened because the test expected some data or response, but it became empty while running.
> Looks like the code called something more times than expected."

Or even simpler:

> "The test was expecting another value, but there was no value left, so it failed with `StopIteration`."

---

### Second Error

```text
Expected 'remove_redirect_url' to not have been called.
Called 1 times.
```

You can say:

> "The second test expected `remove_redirect_url` should not run when `sg_connect` is empty or None.
> But during execution the function still got called once, so the test failed. (assertion issue)"

---

### Final Overall Summary

> "Only 2 tests failed out of more than 1100 tests.
> Both failures are related to workspace delete functionality."

This is enough for a team meeting and sounds natural for a new joiner.
