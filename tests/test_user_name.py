"""The [persona] user_name setting: what C.A.R.L.A. calls the user.

Blank (the default) means she calls you "Psycho". A legacy ``user_title``
from older configs is honoured when ``user_name`` is blank, and
``user_name`` wins when both are set.
"""

from __future__ import annotations

from psycho_carla.channels.bridge import ChannelBridge
from psycho_carla.core.config import PersonaConfig, config_from_dict
from psycho_carla.core.persona import Persona
from psycho_carla.providers.adapters.local_fallback import LocalFallbackAdapter


def test_default_address_is_psycho():
    assert PersonaConfig().address_name == "Psycho"
    assert Persona().boss == "Psycho"
    assert "Psycho" in Persona().salutation("greeting")


def test_user_name_override():
    cfg = PersonaConfig(user_name="Alex")
    assert cfg.address_name == "Alex"
    assert Persona(cfg).salutation("success") == "Done, Alex."
    assert "Alex" in Persona(cfg).salutation("greeting")


def test_user_name_is_trimmed_and_blank_falls_back():
    assert PersonaConfig(user_name="  Sam  ").address_name == "Sam"
    assert PersonaConfig(user_name="   ").address_name == "Psycho"


def test_legacy_user_title_still_honoured():
    assert PersonaConfig(user_title="Chief").address_name == "Chief"
    # ...but the new setting wins when both are present.
    assert PersonaConfig(user_name="Alex", user_title="Chief").address_name == "Alex"


def test_config_dict_parsing():
    cfg = config_from_dict({"persona": {"user_name": "Riley"}})
    assert cfg.persona.address_name == "Riley"
    cfg = config_from_dict({})
    assert cfg.persona.address_name == "Psycho"


def test_template_exposes_user_name_not_boss():
    from psycho_carla.config_template import TEMPLATE

    assert "user_name" in TEMPLATE
    assert '"Boss"' not in TEMPLATE


def test_generated_config_roundtrip(tmp_path):
    from psycho_carla.core.config import CarlaConfig, dump_toml, load_config

    cfg = CarlaConfig()
    cfg.persona.user_name = "Jo"
    path = tmp_path / "psycho_carla.toml"
    path.write_text(dump_toml(cfg))
    loaded = load_config(path)
    assert loaded.persona.address_name == "Jo"


class _StubPersona:
    boss = "Alex"


class _StubRuntime:
    persona = _StubPersona()


def test_channel_bridge_uses_configured_name():
    bridge = ChannelBridge({}, runtime=_StubRuntime())
    assert bridge._user == "Alex"


def test_channel_bridge_default_is_psycho():
    bridge = ChannelBridge({}, runtime=None)
    assert bridge._user == "Psycho"


def test_local_fallback_default_name():
    assert LocalFallbackAdapter({}).persona == "Psycho"
    assert LocalFallbackAdapter({"persona": "Alex"}).persona == "Alex"


def test_scheduler_agent_uses_configured_name():
    from psycho_carla.agents.builtin import SchedulerAgent

    agent = SchedulerAgent.__new__(SchedulerAgent)
    agent.persona = Persona(PersonaConfig(user_name="Alex"))
    assert agent.describe_schedule([]) == "No jobs scheduled, Alex."


def test_router_fallback_uses_configured_name():
    """The offline fallback is built by the router — it must get the name too."""
    from psycho_carla.core.config import load_config
    from psycho_carla.providers.base import Message
    from psycho_carla.providers.router import ModelRouter

    config = load_config()
    config.persona.user_name = "Alex"
    tier = config.intelligence.tiers["fast_tier"]
    tier.provider = "ollama"
    tier.base_url = "http://127.0.0.1:1"  # nothing listens here
    tier.extra = {"base_url": "http://127.0.0.1:1"}

    router = ModelRouter(config)
    result = router.generate(
        [Message(role="user", content="Hello")], capability_tier="fast_tier"
    )
    assert result.provider == "local_fallback"
    assert "Alex" in result.text
    assert "Psycho" not in result.text
