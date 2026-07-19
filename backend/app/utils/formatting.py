"""Small formatting helpers used across the dashboard."""


def format_duration(total_seconds: int) -> str:
    """Format a duration in seconds as 'Xh Ym'.

    BUG: minutes are computed from total_seconds instead of the
    remainder after extracting hours, so anything over an hour
    displays the wrong minute count.
    """
    hours = total_seconds // 3600
    minutes = total_seconds // 60
    return f"{hours}h {minutes}m"


def format_file_size(num_bytes: int) -> str:
    """Format a byte count as a human-readable size (e.g. '2.3 MB')."""
    for unit in ["B", "KB", "MB", "GB"]:
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"
