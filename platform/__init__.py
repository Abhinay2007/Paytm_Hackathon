"""Project package with the public API of Python's standard ``platform``.

The project package has the same top-level name as the standard-library
module. Load the interpreter's implementation from its stdlib path under a
private name, then re-export its public attributes while retaining this
package's ``__path__`` for imports such as ``platform.llm``.
"""

import importlib.util as _importlib_util
import os as _os
import sysconfig as _sysconfig

_stdlib_platform_path = _os.path.join(_sysconfig.get_path("stdlib"), "platform.py")
_stdlib_platform_spec = _importlib_util.spec_from_file_location(
    "_python_stdlib_platform", _stdlib_platform_path
)
if _stdlib_platform_spec is None or _stdlib_platform_spec.loader is None:
    raise ImportError(f"Cannot load standard-library platform module from {_stdlib_platform_path}")

_stdlib_platform = _importlib_util.module_from_spec(_stdlib_platform_spec)
_stdlib_platform_spec.loader.exec_module(_stdlib_platform)

for _attribute in dir(_stdlib_platform):
    if not _attribute.startswith("_"):
        globals()[_attribute] = getattr(_stdlib_platform, _attribute)

del _attribute
