from __future__ import annotations

from enum import Enum


class ScopeType(str, Enum):
    PERSONAL = "personal"
    GROUP = "group"


class PrimaryAction(str, Enum):
    IGNORE = "ignore"
    REPLY = "reply"
    ACT = "act"
    SCHEDULE = "schedule"


class SecondaryAction(str, Enum):
    REMEMBER = "remember"
    UPDATE_MEMORY = "update_memory"
    LINK_MEMORY = "link_memory"
    RECORD_FEEDBACK = "record_feedback"
    CREATE_TASK = "create_task"
    CREATE_REMINDER = "create_reminder"
    RESCHEDULE = "reschedule"
    CANCEL_REMINDER = "cancel_reminder"


class EventType(str, Enum):
    PRIVATE_MESSAGE = "private_message"
    GROUP_MESSAGE = "group_message"
    REPLY_TO_BOT = "reply_to_bot"
    DIRECT_MENTION = "direct_mention"
    COMMAND = "command"
    EDITED_MESSAGE = "edited_message"
    REACTION_ADDED = "reaction_added"
    REACTION_REMOVED = "reaction_removed"
    REPLY_TO_BOT_MESSAGE = "reply_to_bot_message"
    NEGATIVE_FEEDBACK = "negative_feedback"
    MUTE_REQUEST = "mute_request"
    REMINDER_DUE = "reminder_due"
    COMMITMENT_DUE = "commitment_due"
    FOLLOWUP_DUE = "followup_due"
    SCHEDULED_SUPPORT_MESSAGE = "scheduled_support_message"
    GROUP_SILENCE_WAKEUP = "group_silence_wakeup"
    MEMORY_CANDIDATE_DETECTED = "memory_candidate_detected"
    MEMORY_CONFLICT_DETECTED = "memory_conflict_detected"
    MEMORY_EXPIRED = "memory_expired"
    PATTERN_THRESHOLD_REACHED = "pattern_threshold_reached"


class ResponseMode(str, Enum):
    ASSISTANT = "assistant"
    COACH = "coach"
    MIRROR = "mirror"
    CARE = "care"
    OBSERVER = "observer"
    EXECUTION = "execution"
    GROUP_DIRECT_REPLY = "group_direct_reply"
    GROUP_BANTER = "group_banter"
    GROUP_ROAST = "group_roast"
    GROUP_CALLBACK = "group_callback"
    GROUP_HELP = "group_help"
    GROUP_ORGANIZER = "group_organizer"
    GROUP_ARBITER = "group_arbiter"


class MemoryStatus(str, Enum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    ARCHIVED = "archived"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class MemoryOrigin(str, Enum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    SYSTEM = "system"


class ReasonCode(str, Enum):
    DIRECT_MENTION = "direct_mention"
    REPLY_TO_BOT = "reply_to_bot"
    QUESTION_TO_BOT = "question_to_bot"
    CALLBACK_OPPORTUNITY = "callback_opportunity"
    CONTRADICTION = "contradiction"
    ROAST_OPPORTUNITY = "roast_opportunity"
    HELP_OPPORTUNITY = "help_opportunity"
    RUNNING_JOKE = "running_joke"
    BROKEN_COMMITMENT = "broken_commitment"
    STATEMENT_WATCH = "statement_watch"
    SCHEDULED_REMINDER = "scheduled_reminder"
    COOLDOWN_ACTIVE = "cooldown_active"
    SILENCE_REQUESTED = "silence_requested"
    SERIOUS_CONTEXT = "serious_context"
    SENSITIVE_CONTEXT = "sensitive_context"
    HARD_DAILY_LIMIT = "hard_daily_limit"
    SOFT_DAILY_LIMIT = "soft_daily_limit"
    BOT_SPOKE_RECENTLY = "bot_spoke_recently"
    PREVIOUS_UNSOLICITED_IGNORED = "previous_unsolicited_ignored"
    PERSONAL_DEFAULT_REPLY = "personal_default_reply"
    GROUP_DEFAULT_SILENCE = "group_default_silence"
    HIGH_INITIATIVE = "high_initiative"
    SILENCE_REENGAGEMENT = "silence_reengagement"
