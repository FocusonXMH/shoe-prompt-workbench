"""Server-side workbench policy. Never trust roles supplied by the browser."""
from dataclasses import dataclass


class Forbidden(Exception):
    pass


@dataclass(frozen=True)
class WorkbenchUser:
    user_id: str
    is_admin: bool = False


def require_admin(user: WorkbenchUser) -> None:
    if not user.is_admin:
        raise Forbidden('administrator permission required')


def can_transition_feedback(user: WorkbenchUser, author_id: str, current: str, target: str) -> bool:
    """Administrators may resolve directly; authors may reopen after resolution."""
    admin_transitions = {('pending', 'processing'), ('processing', 'awaiting_confirmation'),
                         ('pending', 'resolved'), ('processing', 'resolved'),
                         ('awaiting_confirmation', 'resolved'),
                         ('awaiting_confirmation', 'processing'), ('resolved', 'processing')}
    author_transitions = {('pending', 'withdrawn'), ('processing', 'withdrawn'),
                          ('withdrawn', 'pending'), ('awaiting_confirmation', 'resolved'),
                          ('awaiting_confirmation', 'processing'), ('resolved', 'processing')}
    return ((user.is_admin and (current, target) in admin_transitions) or
            (user.user_id == author_id and (current, target) in author_transitions))


def feedback_author_name(user: WorkbenchUser, author_id: str, display_name: str, anonymous: bool) -> str:
    if anonymous and not user.is_admin and user.user_id != author_id:
        return '匿名用户'
    return display_name
