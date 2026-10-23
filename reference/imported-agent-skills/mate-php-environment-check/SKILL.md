---
name: mate-php-environment-check
description: Check PHP version, OS and loaded extensions when a diagnostic fails or a runtime capability is missing.
---

Read the installed schema with `framework_inspect(kind="mate", name="server-info")`,
then execute with `parameters={}`. The result identifies PHP version, OS and loaded
extensions for the runtime executing the diagnostic. It may differ from the web SAPI.

Compare only facts relevant to the failure. Container inspection uses SimpleXML;
a missing `simplexml` extension can explain parsing failures or an empty service map.
An extension being installed does not prove it is loaded in this runtime. Compare
PHP version with the installed package's requirements before blaming application code.
Use package inspection for dependency versions and profiler/service skills for app
behavior. Report missing setup through `guidance_request`.
