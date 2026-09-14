"""Phase 3 publishing queue: approved content -> queue -> schedule -> (Phase 4)
platform publisher. See queue.py for the lifecycle and dispatch.py for the
publisher seam; nothing in this package can post to a social platform while
`auto_publish` is false."""
