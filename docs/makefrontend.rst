
.. _makefrontend:

================
makefrontend CLI
================

RxDjango provides the `makefrontend` CLI command in django to automatically
generate typescript interfaces and classes. It exits with a non-zero status
in case there are modification, and zero in case there are none.

To monitor filesystem for changes and build frontend files automatically on changes,
use the option `--makefrontend` for the runserver command.


--dry-run
---------

Show changes that would be applied, but do not write them

--quiet
-------

Do not output logs

--force
-------

Rebuild all files regardless of changes.
