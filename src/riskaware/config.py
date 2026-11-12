"""Configuration: risk profiles, YAML loading and environment variables.

Search order for the config file: an explicit path, ``./riskaware.yaml``,
``~/.riskaware/config.yaml``. Missing keys fall back to the defaults below, so an
empty file is a valid config. Secrets (API keys) stay in ``.env`` files or the
environment and are never written into the YAML.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RISKAWARE_HOME = Path(os.environ.get("RISKAWARE_HOME", "~/.riskaware")).expanduser()
CONFIG_FILENAMES = ("riskaware.yaml", "riskaware.yml")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# --------------------------------------------------------------------------------------
# Risk profiles
# --------------------------------------------------------------------------------------


class RiskProfile(_Model):
    """All position-management limits. Fractions are of total account equity."""

    name: str = "balanced"
    cash_floor: float = Field(0.30, ge=0.0, lt=1.0, description="cash + T-bill ETF minimum")
    cash_buffer: float = Field(0.02, ge=0.0, lt=0.5, description="raw cash kept outside the T-bill ETF")
    treasury_sleeve: float = Field(0.05, ge=0.0, lt=1.0, description="intermediate Treasury ETF target")
    treasury_band: float = Field(0.02, ge=0.0, description="rebalance the sleeve when drift exceeds this")
    max_position: float = Field(0.07, gt=0.0, le=1.0)
    max_sector: float = Field(0.25, gt=0.0, le=1.0)
    max_positions: int = Field(15, ge=1)
    speculative_sleeve: float = Field(0.03, ge=0.0, le=1.0)
    speculative_max_names: int = Field(2, ge=0)
    speculative_vol: float = Field(0.60, gt=0.0, description="annualized volatility that marks a name speculative")
    speculative_min_price: float = 5.0
    min_conviction: float = Field(0.58, ge=0.0, le=1.0)
    tranches: list[float] = Field(default_factory=lambda: [0.5, 0.25, 0.25])
    tranche_min_gap_days: int = 5
    tranche_min_drop_atr: float = 1.0
    min_quality_for_dip: float = 60.0
    take_profit_trigger: float = 0.25
    take_profit_fraction: float = 0.33
    take_profit_atr_ext: float = 4.0
    take_profit_rsi: float = 78.0
    trend_exit_enabled: bool = True
    trend_exit_min_gain: float = 0.10
    trend_exit_fraction: float = 0.5
    hard_stop: float | None = 0.30
    allow_emergency_exits: bool = True
    min_hold_days: int = 10
    earnings_blackout_days: int = 3
    breakouts_enabled: bool = False
    risk_per_position: float = Field(0.0075, gt=0.0, description="equity at risk per 2-ATR move")
    max_new_positions_per_day: int = 3
    max_buys_per_day: int = 6
    drawdown_pause: float | None = 0.20
    drawdown_pause_days: int = 10
    min_order_value: float = 100.0
    regime_size: dict[str, float] = Field(
        default_factory=lambda: {"RISK_ON": 1.0, "NEUTRAL": 0.75, "UNCERTAIN": 0.5, "RISK_OFF": 0.35}
    )

    @field_validator("tranches")
    @classmethod
    def _tranches(cls, v: list[float]) -> list[float]:
        if not v or any(x <= 0 for x in v):
            raise ValueError("tranches must be positive fractions")
        total = sum(v)
        return [x / total for x in v]

    @model_validator(mode="after")
    def _limits(self) -> RiskProfile:
        if self.cash_floor + self.treasury_sleeve >= 0.95:
            raise ValueError("cash_floor + treasury_sleeve leaves no room for stocks")
        if self.max_position > self.max_sector:
            raise ValueError("max_position cannot exceed max_sector")
        return self

    @property
    def max_equity_exposure(self) -> float:
        """Largest share of equity that may sit in stocks (excludes cash and Treasuries)."""
        return 1.0 - self.cash_floor - self.treasury_sleeve

    def size_multiplier(self, regime: str) -> float:
        key = regime.value if hasattr(regime, "value") else str(regime)
        return float(self.regime_size.get(key, 0.5))


PROFILE_PRESETS: dict[str, dict[str, Any]] = {
    "conservative": dict(
        cash_floor=0.30, treasury_sleeve=0.15, max_position=0.05, max_sector=0.20, max_positions=20,
        speculative_sleeve=0.0, speculative_max_names=0, min_conviction=0.65, min_quality_for_dip=70,
        take_profit_trigger=0.20, hard_stop=0.25, breakouts_enabled=False, risk_per_position=0.005,
        max_new_positions_per_day=2, drawdown_pause=0.15,
        regime_size={"RISK_ON": 1.0, "NEUTRAL": 0.7, "UNCERTAIN": 0.4, "RISK_OFF": 0.25},
    ),
    "balanced": dict(),
    "aggressive": dict(
        cash_floor=0.30, treasury_sleeve=0.0, max_position=0.10, max_sector=0.35, max_positions=12,
        speculative_sleeve=0.07, speculative_max_names=3, min_conviction=0.52, min_quality_for_dip=50,
        take_profit_trigger=0.30, hard_stop=0.35, breakouts_enabled=True, risk_per_position=0.01,
        max_new_positions_per_day=4, drawdown_pause=0.30,
        regime_size={"RISK_ON": 1.0, "NEUTRAL": 0.8, "UNCERTAIN": 0.6, "RISK_OFF": 0.45},
    ),
}


def make_profile(name: str, overrides: dict[str, Any] | None = None) -> RiskProfile:
    if name not in PROFILE_PRESETS:
        raise ValueError(f"unknown profile '{name}', choose one of {sorted(PROFILE_PRESETS)}")
    params = {**PROFILE_PRESETS[name], **(overrides or {}), "name": name}
    return RiskProfile(**params)


# --------------------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------------------


class BrokerConfig(_Model):
    name: str = "paper"
    options: dict[str, Any] = Field(default_factory=dict)


class UniverseConfig(_Model):
    preset: str | None = "megacap"
    symbols: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    benchmark: str = "SPY"
    cash_vehicle: str | None = "SGOV"
    treasury_symbol: str = "IEF"
    speculative_symbols: list[str] = Field(default_factory=list)
    sectors: dict[str, str] = Field(default_factory=dict)

    @field_validator("symbols", "exclude", "speculative_symbols")
    @classmethod
    def _upper(cls, v: list[str]) -> list[str]:
        return [s.strip().upper() for s in v if s and s.strip()]

    def resolve(self) -> list[str]:
        from .universe import preset_symbols

        out: list[str] = []
        if self.preset:
            for name in self.preset.split("+"):
                out.extend(preset_symbols(name.strip()))
        out.extend(self.symbols)
        seen: set[str] = set()
        result = []
        for s in out:
            if s not in seen and s not in self.exclude:
                seen.add(s)
                result.append(s)
        return result


class DataConfig(_Model):
    cache_dir: str = str(RISKAWARE_HOME / "cache")
    price_source: Literal["yfinance", "csv"] = "yfinance"
    csv_dir: str | None = None
    history_days: int = 600
    contact_email: str | None = Field(
        None, description="SEC EDGAR and BLS ask automated clients to identify themselves"
    )
    timeout: float = 20.0
    fundamentals: bool = True
    sec_point_in_time: bool = False

    @property
    def cache_path(self) -> Path:
        return Path(self.cache_dir).expanduser()


class EventConfig(_Model):
    enabled: bool = True
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "FOMC": 1.0, "CPI": 0.8, "NFP": 0.7, "PCE": 0.5, "PPI": 0.4, "GDP": 0.4, "OPEX": 0.2,
        }
    )
    windows: dict[str, list[int]] = Field(
        default_factory=lambda: {
            # trading-day offsets around the release; decisions run near the close, so a
            # pre-market release (CPI, jobs) is already known on its own day
            "FOMC": [-2, 0], "CPI": [-1, -1], "NFP": [-1, -1], "PCE": [-1, -1], "PPI": [-1, -1],
            "GDP": [-1, -1], "OPEX": [0, 0],
        }
    )
    uncertain_threshold: float = 0.7
    refresh_online: bool = True


class MacroConfig(_Model):
    vix_uncertain: float = 20.0
    vix_risk_off: float = 30.0
    backwardation_ratio: float = 1.0
    flat_term_ratio: float = 0.97
    fear_uncertain: float = 35.0
    fear_extreme: float = 15.0
    breadth_risk_on: float = 0.55
    credit_stress_z: float = -2.0
    use_cnn_fear_greed: bool = True
    events: EventConfig = Field(default_factory=EventConfig)


class NewsConfig(_Model):
    enabled: bool = True
    finviz: bool = True
    feeds: list[str] = Field(default_factory=lambda: ["google", "nasdaq", "seekingalpha", "yahoo", "sec"])
    lookback_hours: int = 72
    min_independent_sources: int = 2
    unconfirmed_weight: float = 0.2
    similarity_threshold: float = 0.34
    max_symbols_per_run: int = 25
    request_interval: float = 1.0


class ScoringConfig(_Model):
    technical: float = 0.55
    fundamental: float = 0.20
    news: float = 0.15
    macro: float = 0.10

    @model_validator(mode="after")
    def _normalize(self) -> ScoringConfig:
        total = self.technical + self.fundamental + self.news + self.macro
        if total <= 0:
            raise ValueError("scoring weights must sum to a positive number")
        return self

    def weights(self) -> dict[str, float]:
        total = self.technical + self.fundamental + self.news + self.macro
        return {
            "technical": self.technical / total,
            "fundamental": self.fundamental / total,
            "news": self.news / total,
            "macro": self.macro / total,
        }


class LLMConfig(_Model):
    """Optional bull/bear/judge review. provider "anthropic" uses the official SDK (key from
    ANTHROPIC_API_KEY or an `ant auth login` profile); "openai" means any OpenAI-compatible
    endpoint (OpenAI, DeepSeek, Ollama, vLLM, OpenRouter, ...) set through base_url."""

    enabled: bool = False
    provider: Literal["openai", "anthropic"] = "anthropic"
    model: str = "claude-opus-5-5"
    base_url: str | None = None
    api_key_env: str = "OPENAI_API_KEY"
    max_adjustment: float = Field(0.15, ge=0.0, le=0.5)
    temperature: float = 0.2  # OpenAI-compatible endpoints only
    timeout: float = 120.0
    max_symbols_per_run: int = 10
    refusal_fallback: bool = True


class ExecutionConfig(_Model):
    mode: Literal["dry-run", "paper", "live"] = "paper"
    order_type: Literal["LIMIT", "MARKET"] = "LIMIT"
    limit_offset: float = Field(0.003, ge=0.0, le=0.05)
    price_band: float = Field(0.05, gt=0.0, le=0.5)
    max_orders_per_run: int = 12
    max_order_value: float | None = None
    allow_fractional: bool = False
    extended_hours: bool = False
    run_time: str = "15:40"
    timezone: str = "America/New_York"
    require_market_day: bool = True
    confirm_live: bool = True
    manage_cash_vehicle: bool = True


class NotifyConfig(_Model):
    telegram_bot_token_env: str = "TELEGRAM_BOT_TOKEN"
    telegram_chat_id_env: str = "TELEGRAM_CHAT_ID"
    discord_webhook_env: str = "DISCORD_WEBHOOK_URL"
    slack_webhook_env: str = "SLACK_WEBHOOK_URL"
    events: list[str] = Field(default_factory=lambda: ["orders", "errors", "regime"])


class StrategyConfig(_Model):
    name: str = "risk_aware"
    params: dict[str, Any] = Field(default_factory=dict)


class BacktestConfig(_Model):
    start: str = "2018-01-01"
    end: str | None = None
    initial_cash: float = 100_000.0
    commission_per_share: float = 0.0
    commission_min: float = 0.0
    slippage_bps: float = 5.0
    max_chase: float = 0.01
    cash_yield_symbol: str | None = "BIL"
    output_dir: str = "backtests"
    warmup_days: int = 320


class AppConfig(_Model):
    profile: str = "balanced"
    profile_overrides: dict[str, Any] = Field(default_factory=dict)
    language: Literal["en", "zh"] = "en"
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    macro: MacroConfig = Field(default_factory=MacroConfig)
    news: NewsConfig = Field(default_factory=NewsConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)

    source_path: str | None = Field(None, exclude=True)

    @field_validator("profile")
    @classmethod
    def _profile(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in PROFILE_PRESETS:
            raise ValueError(f"profile must be one of {sorted(PROFILE_PRESETS)}")
        return v

    def risk_profile(self) -> RiskProfile:
        return make_profile(self.profile, self.profile_overrides)


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _interpolate(value: Any) -> Any:
    """Replace ${VAR} and ${VAR:-default} in strings with environment values."""
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


def load_dotenv_files(extra: list[Path] | None = None) -> list[Path]:
    """Load .env files without overriding variables that are already set."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return []
    loaded = []
    candidates = [Path.cwd() / ".env", RISKAWARE_HOME / ".env", *(extra or [])]
    for p in candidates:
        if p.is_file():
            load_dotenv(p, override=False)
            loaded.append(p)
    return loaded


def find_config_file(explicit: str | Path | None = None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"config file not found: {p}")
        return p
    env = os.environ.get("RISKAWARE_CONFIG")
    if env and Path(env).expanduser().is_file():
        return Path(env).expanduser()
    for name in CONFIG_FILENAMES:
        p = Path.cwd() / name
        if p.is_file():
            return p
    p = RISKAWARE_HOME / "config.yaml"
    return p if p.is_file() else None


def load_config(path: str | Path | None = None, overrides: dict[str, Any] | None = None) -> AppConfig:
    load_dotenv_files()
    file = find_config_file(path)
    raw: dict[str, Any] = {}
    if file is not None:
        with open(file, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{file} must contain a mapping at the top level")
    raw = _interpolate(raw)
    if overrides:
        raw = deep_merge(raw, overrides)
    cfg = AppConfig(**raw)
    cfg.source_path = str(file) if file else None
    return cfg


def deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def env(name: str | None, default: str | None = None) -> str | None:
    if not name:
        return default
    val = os.environ.get(name)
    return val if val not in (None, "") else default
