"""Seed demo agent-usage data: extra pages, 2 agents, simulated multi-session consumption.

Creates:
- 20 extra pages, extending seed_db.py's animal/plant hierarchy (11 more mammals,
  9 more plants)
- 2 agents ("research assistant" and "support bot" personas)
- 2 bundles (animals-themed, plants-themed)
- ~3 sessions per agent, each a short burst of search/fetch/scan/bundle calls, with
  results computed via the real agent_consumption.py service calls and logged to
  Elasticsearch (RetrievalLogEntry) with timestamps jittered across the past 14 days,
  so a usage dashboard has real data + a real time series to render.

Prerequisite: python scripts/seed_db.py must have been run first (needs admin1 and
the animals/mammals/plants pages). Does not clear any existing data - purely additive.
Requires ES_AUDIT_ENABLED=true (otherwise there's nowhere for consumption events to go).

Run: python scripts/seed_agent_usage.py
Respects DB_BACKEND env var (mongodb / postgres).
"""

import asyncio
import random
import sys
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.models.agent import Agent, AgentCreate
from app.models.bundle import BundleEntry, ContentForm
from app.models.page import HistoryEntry, Page, PageStatus, Reference, ReferenceType
from app.models.retrieval_log import ConsumptionMode, MissCandidate, RetrievalLogEntry, UnavailablePageLog
from app.search_config import ACTIVE_SEARCH_CANDIDATE_POOL, ACTIVE_SEARCH_MISS_THRESHOLD, ACTIVE_SEARCH_TOP_N
from app.services.agent_provisioning import create_agent
from app.services.bundles import fetch_bundle_text, upsert_bundle
from app.services.event_log import emit_retrieval_log
from app.services.pages import find_page_docs_fuzzy_scored, get_page
from app.services.passive_scan import scan_text
from app.services.permissions import can_view_page

_NOW = datetime.now(timezone.utc)
SIMULATION_WINDOW_DAYS = 14


def _init_history(action: str = "create", snapshot: str = "Initial creation") -> list[HistoryEntry]:
    return [HistoryEntry(timestamp=_NOW, user_id="admin1", action=action, snapshot=snapshot)]


def _page(page_id, title, description, parent_id, content, tags, aliases=None) -> Page:
    return Page(
        page_id=page_id,
        title=title,
        description=description,
        parent_id=parent_id,
        content=content,
        references=[Reference(type=ReferenceType.page, page_id=parent_id)],
        aliases=aliases or [],
        tags=tags,
        status=PageStatus.published,
        created_by="admin1",
        created_at=_NOW,
        updated_at=_NOW,
        history=_init_history(),
    )


NEW_MAMMAL_PAGES = [
    _page("elephant", "פיל", "פיל — Loxodonta", "mammals",
          "# פיל\n\nהפיל הוא היונק היבשתי הגדול ביותר.\n\n## מאפיינים\n- חדק ארוך\n- חוכמה גבוהה\n- חיה עדרית",
          ["יונקים"], ["Loxodonta"]),
    _page("lion", "אריה", "אריה — Panthera leo", "mammals",
          "# אריה\n\nהאריה הוא חתולי גדול החי בעדרים הנקראים גאווה.\n\n## מאפיינים\n- רעמה אצל הזכרים\n- ציד קבוצתי",
          ["יונקים"], ["Panthera leo"]),
    _page("tiger", "נמר", "נמר — Panthera tigris", "mammals",
          "# נמר\n\nהנמר הוא החתולי הגדול ביותר, בעל פסים ייחודיים.\n\n## מאפיינים\n- פסים כתומים ושחורים\n- חיה בודדה",
          ["יונקים"], ["Panthera tigris"]),
    _page("horse", "סוס", "סוס — Equus ferus caballus", "mammals",
          "# סוס\n\nהסוס הוא יונק מבויית המשמש לרכיבה ועבודה.\n\n## מאפיינים\n- ריצה מהירה\n- חיה עדרית",
          ["יונקים", "חיות משק"], ["Equus ferus caballus"]),
    _page("rabbit", "ארנב", "ארנב — Oryctolagus cuniculus", "mammals",
          "# ארנב\n\nהארנב הוא יונק קטן בעל אוזניים ארוכות.\n\n## מאפיינים\n- קפיצה מהירה\n- התרבות מהירה",
          ["יונקים", "חיות בית"], ["Oryctolagus cuniculus"]),
    _page("mouse", "עכבר", "עכבר — Mus musculus", "mammals",
          "# עכבר\n\nהעכבר הוא מכרסם קטן הנפוץ בכל העולם.\n\n## מאפיינים\n- גודל קטן\n- התרבות מהירה מאוד",
          ["יונקים"], ["Mus musculus"]),
    _page("bear", "דוב", "דוב — Ursidae", "mammals",
          "# דוב\n\nהדוב הוא יונק טורף גדול, חלקם עוברים תרדמת חורף.\n\n## מאפיינים\n- כוח פיזי רב\n- תזונה כל-אוכלת",
          ["יונקים"], ["Ursidae"]),
    _page("deer", "צבי", "צבי — Cervidae", "mammals",
          "# צבי\n\nהצבי הוא יונק מעלה גרה, ידוע בקרניו המסועפות.\n\n## מאפיינים\n- ריצה זריזה\n- קרניים אצל הזכרים",
          ["יונקים"], ["Cervidae"]),
    _page("fox", "שועל", "שועל — Vulpes vulpes", "mammals",
          "# שועל\n\nהשועל הוא יונק טורף קטן ממשפחת הכלביים, ידוע בערמומיותו.\n\n## מאפיינים\n- זנב שעיר\n- פעיל בלילה",
          ["יונקים"], ["Vulpes vulpes"]),
    _page("wolf", "זאב", "זאב — Canis lupus", "mammals",
          "# זאב\n\nהזאב הוא אבי הטיפוס של הכלב הביתי, חי בלהקות.\n\n## מאפיינים\n- חיה עדרית\n- ציד קבוצתי",
          ["יונקים"], ["Canis lupus"]),
    _page("monkey", "קוף", "קוף — Simiiformes", "mammals",
          "# קוף\n\nהקוף הוא יונק פרימטי, קרוב מבחינה אבולוציונית לאדם.\n\n## מאפיינים\n- זריזות גבוהה\n- חיה חברתית",
          ["יונקים"], ["Simiiformes"]),
]

NEW_PLANT_PAGES = [
    _page("rose", "ורד", "ורד — Rosa", "plants",
          "# ורד\n\nהוורד הוא שיח פורח הידוע בניחוחו וביופיו.\n\n## מאפיינים\n- קוצים על הגבעול\n- פריחה צבעונית",
          ["צמחים", "בוטניקה"], ["Rosa"]),
    _page("oak", "אלון", "אלון — Quercus", "plants",
          "# אלון\n\nהאלון הוא עץ נשיר גדול וארוך חיים.\n\n## מאפיינים\n- עץ נשיר\n- מייצר בלוטים",
          ["צמחים", "בוטניקה"], ["Quercus"]),
    _page("cactus", "קקטוס", "קקטוס — Cactaceae", "plants",
          "# קקטוס\n\nהקקטוס הוא צמח מדברי המאחסן מים ברקמותיו.\n\n## מאפיינים\n- עמידות לבצורת\n- קוצים במקום עלים",
          ["צמחים", "בוטניקה"], ["Cactaceae"]),
    _page("wheat", "חיטה", "חיטה — Triticum", "plants",
          "# חיטה\n\nהחיטה היא דגן המהווה מרכיב יסוד בתזונת האדם.\n\n## מאפיינים\n- גידול שדה\n- בסיס לקמח",
          ["צמחים", "בוטניקה"], ["Triticum"]),
    _page("tomato", "עגבנייה", "עגבנייה — Solanum lycopersicum", "plants",
          "# עגבנייה\n\nהעגבנייה היא ירק-פרי נפוץ בבישול.\n\n## מאפיינים\n- צבע אדום\n- עשירה בליקופן",
          ["צמחים", "בוטניקה"], ["Solanum lycopersicum"]),
    _page("orchid", "סחלב", "סחלב — Orchidaceae", "plants",
          "# סחלב\n\nהסחלב הוא צמח פורח נוי בעל מגוון צורות עצום.\n\n## מאפיינים\n- פריחה מרהיבה\n- אפיפיטי לרוב",
          ["צמחים", "בוטניקה"], ["Orchidaceae"]),
    _page("bamboo", "במבוק", "במבוק — Bambusoideae", "plants",
          "# במבוק\n\nהבמבוק הוא דגן עצי הגדל מהר במיוחד.\n\n## מאפיינים\n- קצב גדילה מהיר\n- חוזק מבני גבוה",
          ["צמחים", "בוטניקה"], ["Bambusoideae"]),
    _page("fern", "שרך", "שרך — Polypodiopsida", "plants",
          "# שרך\n\nהשרך הוא צמח עתיק המתרבה באמצעות נבגים.\n\n## מאפיינים\n- ללא פרחים\n- אוהב לחות",
          ["צמחים", "בוטניקה"], ["Polypodiopsida"]),
    _page("tulip", "צבעוני", "צבעוני — Tulipa", "plants",
          "# צבעוני\n\nהצבעוני הוא צמח פקעת פורח, סמל של האביב.\n\n## מאפיינים\n- פקעת תת-קרקעית\n- פריחה עונתית",
          ["צמחים", "בוטניקה"], ["Tulipa"]),
]

NEW_PAGES = NEW_MAMMAL_PAGES + NEW_PLANT_PAGES  # 11 + 9 = 20

AGENT_DEFS = [
    {"slug": "research", "name": "עוזר מחקר"},
    {"slug": "support", "name": "בוט תמיכה"},
]

BUNDLE_DEFS = [
    {
        "name": "חיות-פופולריות",
        "entries": [
            BundleEntry(page_id="dog", content_form=ContentForm.full_info),
            BundleEntry(page_id="cat", content_form=ContentForm.full_info),
            BundleEntry(page_id="lion", content_form=ContentForm.description),
            BundleEntry(page_id="elephant", content_form=ContentForm.description),
        ],
    },
    {
        "name": "צמחים-נפוצים",
        "entries": [
            BundleEntry(page_id="rose", content_form=ContentForm.full_info),
            BundleEntry(page_id="cactus", content_form=ContentForm.full_info),
            BundleEntry(page_id="wheat", content_form=ContentForm.description),
        ],
    },
]


# ---------------------------------------------------------------------------
# Repo bootstrap (pages/users/agents/bundles, both backends) -- background_repos()
# in app/container.py doesn't wire agents/bundles, so we build our own small set.
# ---------------------------------------------------------------------------

@dataclass
class Repos:
    pages: object
    users: object
    agents: object
    bundles: object


@asynccontextmanager
async def get_repos():
    if settings.db_backend == "postgres":
        from app.storage.postgres.agents import PostgresAgentRepository
        from app.storage.postgres.bundles import PostgresBundleRepository
        from app.storage.postgres.pages import PostgresPageRepository
        from app.storage.postgres.users import PostgresUserRepository
        from app.infrastructure.postgres.engine import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            yield Repos(
                pages=PostgresPageRepository(session),
                users=PostgresUserRepository(session),
                agents=PostgresAgentRepository(session),
                bundles=PostgresBundleRepository(session),
            )
            await session.commit()
    else:
        from app.infrastructure.mongo import get_db
        from app.storage.mongo.agents import MongoAgentRepository
        from app.storage.mongo.bundles import MongoBundleRepository
        from app.storage.mongo.pages import MongoPageRepository
        from app.storage.mongo.users import MongoUserRepository

        db = get_db()
        yield Repos(
            pages=MongoPageRepository(db),
            users=MongoUserRepository(db),
            agents=MongoAgentRepository(db),
            bundles=MongoBundleRepository(db),
        )


# ---------------------------------------------------------------------------
# Simulated consumption -- reuses the same underlying calls agent_consumption.py's
# run_search/run_fetch/run_scan/run_bundle_fetch use, but with an explicit backdated
# ts (those functions always stamp ts=now() internally, no override hook).
# ---------------------------------------------------------------------------

def _session_id(slug: str, n: int) -> str:
    return f"sess-{slug}-{n}-{uuid.uuid4().hex[:8]}"


async def _do_search(query: str, agent: Agent, user, session_id: str, ts: datetime, page_repo) -> None:
    t0 = time.perf_counter()
    candidates = await find_page_docs_fuzzy_scored(
        query, fields=["page_id", "title", "description", "trust_tier"],
        user=user, repo=page_repo, limit=ACTIVE_SEARCH_CANDIDATE_POOL,
    )
    candidates.sort(key=lambda d: d["score"], reverse=True)
    good = [c for c in candidates if c["score"] >= ACTIVE_SEARCH_MISS_THRESHOLD][:ACTIVE_SEARCH_TOP_N]
    latency_ms = (time.perf_counter() - t0) * 1000

    if not good:
        entry = RetrievalLogEntry(
            ts=ts, request_id=str(uuid.uuid4()), agent_id=agent.agent_id, agent_name=agent.name, session_id=session_id,
            mode=ConsumptionMode.search, query=query,
            candidates_not_returned=[MissCandidate(page_id=c["page_id"], title=c["title"], score=c["score"]) for c in candidates],
            miss=True, latency_ms=latency_ms,
        )
    else:
        entry = RetrievalLogEntry(
            ts=ts, request_id=str(uuid.uuid4()), agent_id=agent.agent_id, agent_name=agent.name, session_id=session_id,
            mode=ConsumptionMode.search, query=query,
            page_ids=[c["page_id"] for c in good], tiers=[ContentForm.description.value] * len(good),
            scores=[c["score"] for c in good], miss=False, latency_ms=latency_ms,
        )
    emit_retrieval_log(entry)


async def _do_fetch(page_ids: list[str], agent: Agent, user, session_id: str, ts: datetime, page_repo) -> None:
    t0 = time.perf_counter()
    hit_ids, unavailable = [], []
    for pid in page_ids:
        page = await get_page(pid, page_repo)
        if page is None or page.status == PageStatus.deleted:
            unavailable.append(UnavailablePageLog(page_id=pid, reason="not_found"))
            continue
        if not await can_view_page(user, pid, page_repo):
            unavailable.append(UnavailablePageLog(page_id=pid, reason="forbidden"))
            continue
        hit_ids.append(pid)
    latency_ms = (time.perf_counter() - t0) * 1000

    entry = RetrievalLogEntry(
        ts=ts, request_id=str(uuid.uuid4()), agent_id=agent.agent_id, agent_name=agent.name, session_id=session_id,
        mode=ConsumptionMode.fetch, page_ids=hit_ids, tiers=[ContentForm.full_info.value] * len(hit_ids),
        unavailable=unavailable, latency_ms=latency_ms,
    )
    emit_retrieval_log(entry)


async def _do_scan(text: str, agent: Agent, user, session_id: str, ts: datetime, page_repo) -> None:
    t0 = time.perf_counter()
    matches, spans = await scan_text(text, user, page_repo)
    latency_ms = (time.perf_counter() - t0) * 1000

    entry = RetrievalLogEntry(
        ts=ts, request_id=str(uuid.uuid4()), agent_id=agent.agent_id, agent_name=agent.name, session_id=session_id,
        mode=ConsumptionMode.scan, matched_spans=spans, page_ids=[m["page_id"] for m in matches],
        tiers=[ContentForm.description.value] * len(matches), latency_ms=latency_ms,
    )
    emit_retrieval_log(entry)


async def _do_bundle(name: str, agent: Agent, user, session_id: str, ts: datetime, bundle_repo, page_repo) -> None:
    t0 = time.perf_counter()
    try:
        bundle, _rendered = await fetch_bundle_text(name, user, bundle_repo, page_repo)
    except (ValueError, PermissionError) as e:
        entry = RetrievalLogEntry(
            ts=ts, request_id=str(uuid.uuid4()), agent_id=agent.agent_id, agent_name=agent.name, session_id=session_id,
            mode=ConsumptionMode.bundle, bundle_name=name, error=str(e),
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
        emit_retrieval_log(entry)
        return

    latency_ms = (time.perf_counter() - t0) * 1000
    entry = RetrievalLogEntry(
        ts=ts, request_id=str(uuid.uuid4()), agent_id=agent.agent_id, agent_name=agent.name, session_id=session_id,
        mode=ConsumptionMode.bundle, bundle_name=name,
        page_ids=[e.page_id for e in bundle.entries],
        tiers=[e.content_form.value for e in bundle.entries], latency_ms=latency_ms,
    )
    emit_retrieval_log(entry)


def _session_base_ts() -> datetime:
    offset_hours = random.uniform(1, SIMULATION_WINDOW_DAYS * 24)
    return _NOW - timedelta(hours=offset_hours)


def _next_ts(base: datetime, step: int) -> datetime:
    return base + timedelta(minutes=step * random.uniform(1, 4))


RESEARCH_QUERIES = ["אריה", "פיל", "כלב", "וורד גינה שטויות", "נמר"]
RESEARCH_FETCH_SETS = [["lion", "elephant"], ["dog", "wolf"], ["tiger", "does-not-exist"]]
SUPPORT_SCAN_TEXTS = [
    "מישהו שאל על שועל ועל דוב ביער. גם על ורד וסחלב בגינה.",
    "הלקוח התעניין בעכבר מעבדה ובקקטוס למשרד.",
    "אין כאן שום התאמה לטקסט הזה כלל.",
]
SUPPORT_FETCH_SETS = [["rose", "cactus"], ["deer", "fox"], ["tulip"]]


async def simulate_agent(agent_def: dict, agent: Agent, user, repos: Repos) -> int:
    event_count = 0
    for n in range(3):
        session_id = _session_id(agent_def["slug"], n)
        base_ts = _session_base_ts()
        step = 0

        if agent_def["slug"] == "research":
            query = RESEARCH_QUERIES[(n * 2) % len(RESEARCH_QUERIES)]
            await _do_search(query, agent, user, session_id, _next_ts(base_ts, step), repos.pages); step += 1; event_count += 1

            fetch_ids = RESEARCH_FETCH_SETS[n % len(RESEARCH_FETCH_SETS)]
            await _do_fetch(fetch_ids, agent, user, session_id, _next_ts(base_ts, step), repos.pages); step += 1; event_count += 1

            query2 = RESEARCH_QUERIES[(n * 2 + 1) % len(RESEARCH_QUERIES)]
            await _do_search(query2, agent, user, session_id, _next_ts(base_ts, step), repos.pages); step += 1; event_count += 1

            if n == 0:
                await _do_bundle("חיות-פופולריות", agent, user, session_id, _next_ts(base_ts, step), repos.bundles, repos.pages); step += 1; event_count += 1
        else:
            text = SUPPORT_SCAN_TEXTS[n % len(SUPPORT_SCAN_TEXTS)]
            await _do_scan(text, agent, user, session_id, _next_ts(base_ts, step), repos.pages); step += 1; event_count += 1

            fetch_ids = SUPPORT_FETCH_SETS[n % len(SUPPORT_FETCH_SETS)]
            await _do_fetch(fetch_ids, agent, user, session_id, _next_ts(base_ts, step), repos.pages); step += 1; event_count += 1

            if n == 1:
                await _do_bundle("צמחים-נפוצים", agent, user, session_id, _next_ts(base_ts, step), repos.bundles, repos.pages); step += 1; event_count += 1

            text2 = SUPPORT_SCAN_TEXTS[(n + 1) % len(SUPPORT_SCAN_TEXTS)]
            await _do_scan(text2, agent, user, session_id, _next_ts(base_ts, step), repos.pages); step += 1; event_count += 1

    return event_count


async def seed() -> None:
    if settings.db_backend == "postgres":
        from app.infrastructure.postgres.engine import init_engine
        init_engine(settings.postgres_uri)

    if not settings.es_audit_enabled:
        sys.exit("ES_AUDIT_ENABLED is not set - consumption events would have nowhere to go. Enable it in .env first.")

    from app.infrastructure.elasticsearch import close_es, init_es
    es_kwargs = {}
    if settings.es_api_key:
        es_kwargs["api_key"] = settings.es_api_key
    elif settings.es_username and settings.es_password:
        es_kwargs["basic_auth"] = (settings.es_username, settings.es_password)
    if settings.es_cloud_id:
        es_kwargs["cloud_id"] = settings.es_cloud_id
    hosts = [h.strip() for h in settings.es_hosts.split(",") if h.strip()]
    init_es(hosts, **es_kwargs)

    async with get_repos() as repos:
        admin = await repos.users.get("admin1")
        animals = await repos.pages.get("animals")
        mammals = await repos.pages.get("mammals")
        plants = await repos.pages.get("plants")
        if not admin or not animals or not mammals or not plants:
            sys.exit("Prerequisite missing: run `python scripts/seed_db.py` first (needs admin1 + animals/mammals/plants pages).")

        for page in NEW_PAGES:
            await repos.pages.create(page)
        print(f"✓ Created {len(NEW_PAGES)} extra pages ({len(NEW_MAMMAL_PAGES)} mammals, {len(NEW_PLANT_PAGES)} plants)")

        created_agents = []
        for agent_def in AGENT_DEFS:
            resp = await create_agent(AgentCreate(name=agent_def["name"]), admin, repos.agents, repos.users)
            agent = await repos.agents.get(resp.agent_id)
            user = await repos.users.get(resp.user_id)
            created_agents.append((agent_def, agent, user))
            print(f"✓ Created agent '{agent_def['name']}' (agent_id={resp.agent_id}, api_key={resp.api_key})")

        for bd in BUNDLE_DEFS:
            await upsert_bundle(bd["name"], bd["entries"], admin, repos.bundles, repos.pages)
            print(f"✓ Created bundle '{bd['name']}' ({len(bd['entries'])} entries)")

        total_events = 0
        for agent_def, agent, user in created_agents:
            count = await simulate_agent(agent_def, agent, user, repos)
            total_events += count
            print(f"✓ Simulated {count} consumption events for '{agent_def['name']}' across 3 sessions")

    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    print(f"\n--- Summary ---")
    print(f"Backend: {settings.db_backend}")
    print(f"Extra pages: {len(NEW_PAGES)}")
    print(f"Agents: {len(AGENT_DEFS)}")
    print(f"Bundles: {len(BUNDLE_DEFS)}")
    print(f"Consumption events emitted: {total_events} (spread across the past {SIMULATION_WINDOW_DAYS} days)")

    await close_es()
    if settings.db_backend == "postgres":
        from app.infrastructure.postgres.engine import close_engine
        await close_engine()


if __name__ == "__main__":
    asyncio.run(seed())
