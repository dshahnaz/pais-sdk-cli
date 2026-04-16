"""Per-index DELETE isn't in the published Broadcom doc.

When a deployment 404s/405s on `DELETE /knowledge-bases/{kb}/indexes/{idx}`,
the SDK raises `IndexDeleteUnsupported` (subclass of `PaisError`) with
actionable alternatives. The cleanup workflow catches it and offers
"Delete parent KB" / "Purge --strategy recreate" / "← back" instead of
showing a green ✓ banner."""

from __future__ import annotations

from pais.errors import IndexDeleteUnsupported, PaisError, PaisNotFoundError


def test_exception_carries_default_alternatives() -> None:
    """Default constructor populates `suggested_alternatives` with two items."""
    e = IndexDeleteUnsupported()
    assert "DELETE" in str(e)
    assert len(e.suggested_alternatives) == 2
    assert any("KB" in a for a in e.suggested_alternatives)
    assert any("recreate" in a for a in e.suggested_alternatives)


def test_exception_is_a_pais_error() -> None:
    """Callers catching PaisError will also catch IndexDeleteUnsupported."""
    assert issubclass(IndexDeleteUnsupported, PaisError)


def test_indexes_delete_raises_on_404() -> None:
    """When the server returns 404 on the DELETE call, we raise our specific
    exception (not a generic PaisNotFoundError)."""
    from pais.resources.indexes import IndexesResource

    class _BoomTransport:
        def request(self, method: str, path: str, **_: object) -> object:
            raise PaisNotFoundError("simulated missing endpoint", status_code=404)

        def stream(self, *_a: object, **_k: object) -> object:
            raise NotImplementedError

        def close(self) -> None:
            pass

    res = IndexesResource(_BoomTransport())  # type: ignore[arg-type]
    try:
        res.delete("kb_x", "idx_x")
    except IndexDeleteUnsupported as e:
        assert "DELETE" in str(e)
        return
    raise AssertionError("expected IndexDeleteUnsupported")


def test_indexes_delete_raises_on_405() -> None:
    """405 Method Not Allowed → same exception."""
    from pais.resources.indexes import IndexesResource

    class _BoomTransport:
        def request(self, method: str, path: str, **_: object) -> object:
            raise PaisError("method not allowed", status_code=405)

        def stream(self, *_a: object, **_k: object) -> object:
            raise NotImplementedError

        def close(self) -> None:
            pass

    res = IndexesResource(_BoomTransport())  # type: ignore[arg-type]
    try:
        res.delete("kb_x", "idx_x")
    except IndexDeleteUnsupported:
        return
    raise AssertionError("expected IndexDeleteUnsupported")


def test_other_errors_pass_through_unchanged() -> None:
    """A 500 on DELETE is a real server error — surface it, don't pretend it's an unsupported endpoint."""
    from pais.errors import PaisServerError
    from pais.resources.indexes import IndexesResource

    class _BoomTransport:
        def request(self, method: str, path: str, **_: object) -> object:
            raise PaisServerError("internal", status_code=500)

        def stream(self, *_a: object, **_k: object) -> object:
            raise NotImplementedError

        def close(self) -> None:
            pass

    res = IndexesResource(_BoomTransport())  # type: ignore[arg-type]
    try:
        res.delete("kb_x", "idx_x")
    except PaisServerError:
        return
    except IndexDeleteUnsupported as e:
        raise AssertionError("500 should NOT be remapped to IndexDeleteUnsupported") from e
    raise AssertionError("expected PaisServerError")
