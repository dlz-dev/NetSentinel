import os
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage

def setup_langsmith(api_key: str) -> None:
    # j'active le tracing LangSmith — chaque appel LLM sera enregistré automatiquement
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = api_key
    os.environ["LANGCHAIN_PROJECT"] = "netsentinel"


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

    # je crée le modèle Claude via LangChain
    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        api_key=anthropic_api_key,
        max_tokens=400,
    )

    # je construis le prompt avec le contexte des métriques du modèle
    prompt = f"""Tu es un expert en cybersécurité SOC. Notre IDS a détecté l'attaque suivante :

Type d'attaque : {attack_type}
F1 Score       : {f1:.2f}%
Precision      : {precision:.2f}%
Recall         : {recall:.2f}%
Faux négatifs  : {fn:,} connexions non détectées

Réponds en 3 points courts :
1. Qu'est-ce que cette attaque et comment fonctionne-t-elle ?
2. Pourquoi notre modèle obtient-il ces performances sur cette classe ?
3. Quel risque représentent les {fn:,} faux négatifs ?

Sois concis et technique."""

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
    llm = ChatAnthropic(model="claude-haiku-4-5-20251001", api_key=anthropic_api_key, max_tokens=1000)

    # je construis un résumé de toutes les classes pour le rapport global
    classes_summary = "\n".join([
        f"- {c['label']}: F1={c['f1']:.2f}%, FN={c['fn']}"
        for c in sorted(per_class_data, key=lambda x: x['f1'])
    ])

    prompt = f"""Tu es CISO d'une entreprise. Génère un briefing exécutif SOC basé sur ces données IDS :

Flows analysés    : {total_flows:,}
Attaques détectées: {total_attacks:,}
Accuracy globale  : {metrics_dict.get('Accuracy', 0):.2f}%
F1 global         : {metrics_dict.get('F1-Score', 0):.2f}%

Performance par classe (triée par F1 croissant — les plus faibles en premier) :
{classes_summary}

Génère un rapport exécutif avec :
1. **Situation globale** (1-2 phrases)
2. **Menaces critiques** (classes avec F1 < 98% ou FN élevés)
3. **Points forts** du modèle
4. **Recommandations prioritaires** (3 actions concrètes)

Style : professionnel, concis, orienté décision."""

    response = llm.invoke([HumanMessage(content=prompt)])
    return response.content


def chat_soc(
    question: str,
    context: dict,
    anthropic_api_key: str,
    langsmith_api_key: str,
) -> str:
    setup_langsmith(langsmith_api_key)
    llm = ChatAnthropic(model="claude-haiku-4-5-20251001", api_key=anthropic_api_key, max_tokens=400)

    # je fournis le contexte complet du dashboard pour que Claude puisse répondre précisément
    prompt = f"""Tu es un expert SOC/cybersécurité. Voici le contexte de notre IDS NetSentinel :

Accuracy : {context['accuracy']:.2f}% | F1 : {context['f1']:.2f}%
Flows analysés : {context['total_flows']:,} | Attaques : {context['total_attacks']:,}
Classes détectées : {', '.join(context['attack_types'])}

Question de l'analyste : {question}

Réponds de façon précise et actionnable en te basant sur ce contexte."""

    response = llm.invoke([HumanMessage(content=prompt)])
    return response.content
