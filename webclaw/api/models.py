"""Pydantic request models for input validation."""
import re

from pydantic import BaseModel, Field, field_validator, model_validator


class LoginRequest(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.strip().lower()


class SwitchTenantRequest(BaseModel):
    """Select the active hotel / property for a multi-branch user.

    ``tenant_id`` / ``property_id`` may be a property UUID or ``ALL`` (group
    owners only — aggregates reads across the group).
    """

    tenant_id: str | None = Field(default=None, max_length=64)
    property_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def resolve_id(self):
        raw = (self.property_id or self.tenant_id or "").strip()
        if not raw:
            raise ValueError("property_id or tenant_id is required")
        object.__setattr__(self, "tenant_id", raw)
        object.__setattr__(self, "property_id", raw)
        return self


class SetupRequest(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=8)
    full_name: str = ""

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("full_name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        return v.strip()


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)


class UpdateProfileRequest(BaseModel):
    """Self-service profile update for the authenticated user."""

    full_name: str | None = None
    email: str | None = None
    current_password: str | None = None
    new_password: str | None = None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip().lower()

    @field_validator("full_name")
    @classmethod
    def strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip()

    @model_validator(mode="after")
    def password_pair(self):
        if self.new_password and not self.current_password:
            raise ValueError("current_password is required to set a new password")
        if self.current_password and not self.new_password:
            raise ValueError("new_password is required when current_password is provided")
        return self


class RegisterHotelRequest(BaseModel):
    hotel_name: str = Field(min_length=2, max_length=120)
    admin_email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    country: str = Field(default="United States", min_length=2, max_length=80)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    subscription_tier: str = Field(default="starter")

    @model_validator(mode="before")
    @classmethod
    def alias_plan_fields(cls, data):
        """Accept plan_code / subscription_plan as aliases for the selected plan."""
        if not isinstance(data, dict):
            return data
        plan = (
            data.get("plan_code")
            or data.get("subscription_plan")
            or data.get("selected_plan")
            or data.get("subscription_tier")
        )
        if plan:
            data["subscription_tier"] = plan
        return data

    @field_validator("admin_email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("hotel_name", "country", "timezone")
    @classmethod
    def strip_text(cls, v: str) -> str:
        return v.strip()

    @field_validator("currency")
    @classmethod
    def currency_iso(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("subscription_tier")
    @classmethod
    def tier_allowed(cls, v: str) -> str:
        allowed = {"starter", "professional", "business", "enterprise"}
        value = v.strip().lower()
        if value not in allowed:
            raise ValueError("subscription_tier must be starter, business, professional, or enterprise")
        if value == "business":
            return "professional"
        return value


class CreateHotelTenantRequest(BaseModel):
    hotel_name: str = Field(min_length=2, max_length=120)
    admin_email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    country: str = Field(default="United States", min_length=2, max_length=80)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    plan_code: str = Field(default="starter")

    @field_validator("admin_email")
    @classmethod
    def normalize_admin_email(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("hotel_name", "country", "timezone")
    @classmethod
    def strip_tenant_text(cls, v: str) -> str:
        return v.strip()

    @field_validator("currency")
    @classmethod
    def tenant_currency_iso(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("plan_code")
    @classmethod
    def plan_code_allowed(cls, v: str) -> str:
        value = v.strip().lower()
        if value == "professional":
            return "business"
        if value not in {"starter", "business", "enterprise"}:
            raise ValueError("plan_code must be starter, business, or enterprise")
        return value


class UpdateHotelTenantRequest(BaseModel):
    plan_code: str | None = None
    hotel_name: str | None = None
    timezone: str | None = None
    country: str | None = None
    currency: str | None = None

    @field_validator("plan_code")
    @classmethod
    def update_plan_code(cls, v: str | None) -> str | None:
        if v is None:
            return v
        value = v.strip().lower()
        if value == "professional":
            return "business"
        if value not in {"starter", "business", "enterprise"}:
            raise ValueError("plan_code must be starter, business, or enterprise")
        return value


class SubscriptionPlanRequest(BaseModel):
    code: str = Field(min_length=2, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    billing_cycle: str = Field(default="monthly")
    monthly_price: str = Field(default="0")
    annual_price: str = Field(default="0")
    max_rooms: int = Field(default=20, ge=0)
    max_staff: int = Field(default=5, ge=0)
    features: dict = Field(default_factory=dict)
    is_active: bool = True
    sort_order: int = Field(default=0)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, v: str) -> str:
        return v.strip().lower().replace(" ", "_")

    @field_validator("billing_cycle")
    @classmethod
    def cycle_allowed(cls, v: str) -> str:
        value = v.strip().lower()
        if value not in {"monthly", "annual"}:
            raise ValueError("billing_cycle must be monthly or annual")
        return value


class BillingOverrideRequest(BaseModel):
    action: str = Field(min_length=3, max_length=40)
    days: int = Field(default=14, ge=1, le=365)
    discount_percent: float = Field(default=0, ge=0, le=100)
    note: str = Field(default="", max_length=500)

    @field_validator("action")
    @classmethod
    def action_allowed(cls, v: str) -> str:
        value = v.strip().lower()
        if value not in {"extend_trial", "apply_discount", "clear_discount", "clear_trial"}:
            raise ValueError(
                "action must be extend_trial, apply_discount, clear_discount, or clear_trial"
            )
        return value


# Shell metacharacter pattern — reject keys containing these
_SHELL_META_RE = re.compile(r"[;|&`$(){}\\]")
MAX_PARAM_VALUE_LENGTH = 10 * 1024  # 10 KB


def sanitize_skill_params(params: dict) -> dict:
    """Sanitize skill action parameters: strip whitespace, reject dangerous keys, limit value length."""
    clean = {}
    for key, value in params.items():
        # Strip whitespace from keys
        key = str(key).strip()
        if not key:
            continue
        # Reject keys with shell metacharacters
        if _SHELL_META_RE.search(key):
            continue
        # Limit value length for strings
        if isinstance(value, str):
            value = value.strip()
            if len(value) > MAX_PARAM_VALUE_LENGTH:
                value = value[:MAX_PARAM_VALUE_LENGTH]
        clean[key] = value
    return clean
