ROOMS_VIEW_ALL = "rooms:view_all"
ROOMS_VIEW_OWN = "rooms:view_own"
ROOMS_DELETE = "rooms:delete"
QUEUES_VIEW_STATS = "queues:view_stats"
METADATA_EVENTS_VIEW_ALL = "metadata_events:view_all"
CHAT_EXTERNAL_VIEW_ALL = "chat_external:view_all"
AGENT_CONTROL = "agent:control"
RESOURCE_DELETE_ANY = "resource:delete_any"


# Default permissions for new users (regular user)
DEFAULT_USER_PERMISSIONS = [
    ROOMS_VIEW_OWN,
]

# Default permissions for bot accounts
DEFAULT_BOT_PERMISSIONS = [
    METADATA_EVENTS_VIEW_ALL,
    CHAT_EXTERNAL_VIEW_ALL,
]