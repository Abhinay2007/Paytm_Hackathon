import platform

from platform import llm
from platform.llm import chat


def test_stdlib_platform_api_is_available():
    assert platform.system()
    assert platform.python_version()


def test_project_llm_imports_coexist_with_stdlib_api():
    assert callable(chat)
    assert llm.chat is chat
    assert platform.system()
    assert platform.python_version()
