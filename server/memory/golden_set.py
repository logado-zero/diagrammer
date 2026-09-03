"""
The fixture corpus and hand-labelled queries for `python -m server.memory.eval`
— a scaled-down version of DISCUSS_RAG.md §16's "golden set of ~50 queries...
recall@K and MRR... runs in CI" (12 queries here, not 50, since this is a
smoke-level regression gate, not a tuning corpus).

Each query names exactly one relevant corpus entry by key, chosen so the
correct match shares real keywords with the query — this keeps the eval
meaningful even in an environment where the dense encoder isn't available
(the lexical view alone should already find these), while dense/graph still
take part in ranking when they are.
"""

CORPUS: list[dict[str, str]] = [
    {"key": "revenue_q3", "text": "Quarterly revenue for the Da Nang branch reached 4.2 billion VND in Q3."},
    {"key": "warehouse_hanoi", "text": "The Hanoi warehouse stores electronics inventory and ships twice a week."},
    {"key": "shipping_flow", "text": "Order flow: customer places order, warehouse packs it, courier ships it."},
    {"key": "gold_price", "text": "SJC gold sell price rose to 89 million VND per tael on Monday."},
    {"key": "employee_onboarding", "text": "New employee onboarding: sign contract, get laptop, meet the team lead."},
    {"key": "server_outage", "text": "The API server went down for 12 minutes due to a MongoDB connection timeout."},
    {"key": "marketing_budget", "text": "Marketing budget for the Tet campaign was set at 800 million VND."},
    {"key": "customer_complaint", "text": "A customer complained about a delayed shipment from the Saigon depot."},
    {"key": "sales_forecast", "text": "Sales forecast projects a 15 percent increase in Q4 driven by online orders."},
    {"key": "warehouse_safety", "text": "Warehouse safety training covers forklift operation and fire drills."},
    {"key": "chart_export", "text": "Users can export any rendered chart as a PNG from the canvas toolbar."},
    {"key": "onboarding_docs", "text": "Onboarding documents include the employee handbook and the benefits guide."},
]

GOLDEN_QUERIES: list[dict[str, object]] = [
    {"query": "Da Nang branch quarterly revenue", "relevant": ["revenue_q3"]},
    {"query": "Hanoi warehouse electronics inventory", "relevant": ["warehouse_hanoi"]},
    {"query": "how does an order get shipped", "relevant": ["shipping_flow"]},
    {"query": "SJC gold sell price today", "relevant": ["gold_price"]},
    {"query": "new employee onboarding laptop", "relevant": ["employee_onboarding"]},
    {"query": "API server MongoDB timeout outage", "relevant": ["server_outage"]},
    {"query": "Tet campaign marketing budget", "relevant": ["marketing_budget"]},
    {"query": "customer complaint delayed shipment Saigon", "relevant": ["customer_complaint"]},
    {"query": "Q4 sales forecast online orders", "relevant": ["sales_forecast"]},
    {"query": "forklift fire drill safety training", "relevant": ["warehouse_safety"]},
    {"query": "export chart as PNG", "relevant": ["chart_export"]},
    {"query": "employee handbook benefits guide", "relevant": ["onboarding_docs"]},
]
