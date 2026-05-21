import os
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage

_LANGSMITH_OK: bool | None = None  # None = not tested yet


LANGSMITH_ENDPOINT = "https://eu.api.smith.langchain.com"


def _check_langsmith_key(api_key: str) -> bool:
    """Return True if the key can authenticate against LangSmith EU."""
    try:
        import requests as _req
        r = _req.get(
            f"{LANGSMITH_ENDPOINT}/sessions",
            headers={"x-api-key": api_key},
            timeout=5,
        )
        return r.status_code == 200
    except Exception:
        return False


def setup_langsmith(api_key: str) -> None:
    global _LANGSMITH_OK
    if _LANGSMITH_OK is None:
        _LANGSMITH_OK = _check_langsmith_key(api_key)
        if _LANGSMITH_OK:
            print("[LangSmith] ✓ Clé valide — tracing activé (projet : netsentinel, EU)")
        else:
            print(
                "[LangSmith] ✗ Clé invalide — tracing désactivé.\n"
                "  → Vérifiez la clé sur https://eu.smith.langchain.com/settings#api-keys"
            )
    if not _LANGSMITH_OK:
        for var in ("LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING",
                    "LANGCHAIN_API_KEY", "LANGSMITH_API_KEY",
                    "LANGCHAIN_ENDPOINT", "LANGSMITH_ENDPOINT"):
            os.environ.pop(var, None)
        return
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGSMITH_TRACING"]    = "true"
    os.environ["LANGCHAIN_API_KEY"]    = api_key
    os.environ["LANGSMITH_API_KEY"]    = api_key
    os.environ["LANGCHAIN_ENDPOINT"]   = LANGSMITH_ENDPOINT
    os.environ["LANGSMITH_ENDPOINT"]   = LANGSMITH_ENDPOINT
    os.environ["LANGCHAIN_PROJECT"]    = "netsentinel"
    os.environ["LANGSMITH_PROJECT"]    = "netsentinel"


def analyze_threat(
    attack_type: str,
    f1: float,
    precision: float,
    recall: float,
    fn: int,
    anthropic_api_key: str,
    langsmith_api_key: str,
) -> str:
    setup_langsmith(langsmith_api_key)

    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        api_key=anthropic_api_key,
        max_tokens=1024,
    )

    prompt = f"""Tu es un analyste SOC senior. Notre IDS NetSentinel vient de détecter une attaque en temps réel.

Génère une analyse en markdown avec exactement cette structure :

# Analyse — {attack_type}

## 1. Mécanisme de l'attaque
Explique comment fonctionne {attack_type} en 2-3 phrases techniques.

## 2. Performance du modèle
F1 = **{f1:.1f}%** | Précision = **{precision:.1f}%** | Rappel = **{recall:.1f}%** | Faux négatifs = **{fn:,}**

Pourquoi ce score ? Cite 2-3 features réseau discriminantes (ex: `packets_rate`, `syn_flag_counts`).

## 3. Risque & Recommandations SOC
Évalue le risque des {fn:,} faux négatifs. Donne exactement 2 recommandations concrètes numérotées.

Termine par une ligne de statut sous cette forme exacte :
> STATUT : [CRITIQUE / ÉLEVÉ / MODÉRÉ] — [une phrase d'action immédiate]

Sois concis, technique, actionnable."""

    response = llm.invoke([HumanMessage(content=prompt)])
    return response.content


def generate_soc_report(
    metrics_dict: dict,
    per_class_data: list,
    total_flows: int,
    total_attacks: int,
    anthropic_api_key: str,
    langsmith_api_key: str,
) -> str:
    setup_langsmith(langsmith_api_key)

    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        api_key=anthropic_api_key,
        max_tokens=1000,
    )

    classes_summary = "\n".join([
        f"- {c['label']}: F1={c['f1']:.2f}%, FN={c['fn']}"
        for c in sorted(per_class_data, key=lambda x: x['f1'])
    ])

    prompt = f"""Tu es CISO d'une entreprise. Génère un briefing exécutif SOC en markdown :

# Rapport SOC — NetSentinel

## Situation globale
Flows : **{total_flows:,}** | Attaques : **{total_attacks:,}** | Accuracy : **{metrics_dict.get('Accuracy', 0):.1f}%** | F1 : **{metrics_dict.get('F1-Score', 0):.1f}%**

1-2 phrases de situation globale.

## Menaces critiques
Classes avec F1 < 98% ou FN élevés (liste les 2-3 plus préoccupantes avec `code` pour les noms) :
{classes_summary}

## Points forts

## Recommandations prioritaires
3 actions numérotées, concrètes, délai indiqué (T+Xh).

Termine par :
> STATUT GLOBAL : [CRITIQUE / ÉLEVÉ / MODÉRÉ] — [phrase d'action]

Style : professionnel, concis, décisionnel."""

    response = llm.invoke([HumanMessage(content=prompt)])
    return response.content


def chat_soc(
    question: str,
    context: dict,
    anthropic_api_key: str,
    langsmith_api_key: str,
) -> str:
    setup_langsmith(langsmith_api_key)

    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        api_key=anthropic_api_key,
        max_tokens=400,
    )

    prompt = f"""Tu es un expert SOC/cybersécurité. Voici le contexte de notre IDS NetSentinel :

Accuracy : {context['accuracy']:.2f}% | F1 : {context['f1']:.2f}%
Flows analysés : {context['total_flows']:,} | Attaques : {context['total_attacks']:,}
Classes détectées : {', '.join(context['attack_types'])}

Question de l'analyste : {question}

Réponds de façon précise et actionnable en te basant sur ce contexte."""

    response = llm.invoke([HumanMessage(content=prompt)])
    return response.content
