+++ b/backend/app/utils/formatting.py
     displays the wrong minute count.
     """
     hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
     return f"{hours}h {minutes}m"