from unittest.mock import patch

import pytest

import mode.loop
from mode.loop import DEPRECATED_LOOPS, LOOPS


class test_use:
    # NOTE: `importlib.import_module` is patched out throughout.  Actually
    # selecting a backend applies process-wide monkey-patches (gevent and
    # eventlet both patch the stdlib), which would wreck every test that
    # runs afterwards.

    @pytest.mark.parametrize("loop", ["eventlet", "gevent", "uvloop"])
    def test_imports_the_backend_module(self, loop, recwarn):
        # `recwarn` absorbs the gevent deprecation warning quietly.
        with patch("importlib.import_module") as import_module:
            mode.loop.use(loop)
            import_module.assert_called_once_with(LOOPS[loop])

    def test_aio_imports_nothing(self):
        with patch("importlib.import_module") as import_module:
            mode.loop.use("aio")
            import_module.assert_not_called()

    def test_unknown_name_is_treated_as_a_module_path(self):
        with patch("importlib.import_module") as import_module:
            mode.loop.use("my.custom.loop")
            import_module.assert_called_once_with("my.custom.loop")


class test_deprecated_backends:
    def test_gevent_is_deprecated(self):
        assert "gevent" in DEPRECATED_LOOPS

    def test_use_gevent_warns(self):
        with patch("importlib.import_module"):
            with pytest.warns(
                DeprecationWarning, match="gevent loop backend is deprecated"
            ):
                mode.loop.use("gevent")

    def test_warning_precedes_the_import(self):
        # The backend currently fails to import, so the warning is only of
        # any use if it is raised before that happens.
        with patch("importlib.import_module", side_effect=ImportError("boom")):
            with pytest.warns(
                DeprecationWarning, match="gevent loop backend is deprecated"
            ):
                with pytest.raises(ImportError):
                    mode.loop.use("gevent")

    @pytest.mark.parametrize("loop", ["aio", "eventlet", "uvloop"])
    def test_other_backends_do_not_warn(self, loop, recwarn):
        with patch("importlib.import_module"):
            mode.loop.use(loop)
        assert not [
            w
            for w in recwarn.list
            if issubclass(w.category, DeprecationWarning)
        ]
