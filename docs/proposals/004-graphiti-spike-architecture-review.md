# Proposal 004: Graphiti Spike — Architecture Review

- **Status:** Completed — final probe PASS; spike concluded NO
- **Dátum:** 2026-07-10
- **Autor:** Claude (Fable 5) na základe zadania Pavla Pavlovského
- **Vstupy:** [Proposal 002](002-pavol-brain-shared-memory-and-knowledge-graph.md) · [Proposal 003](003-graphiti-spike-design.md) · `spike/` (kód, výsledky, DECISION.md) · **priamo prečítaný zdrojový kód `graphiti-core==0.29.2`** z `spike/.venv`

---

> **Final outcome (2026-07-10):** The final driver/episode probe passed after removing caller-provided episode UUIDs. The subsequent one-record ingest failed in `SummarizedEntities`: the local OpenAI-compatible stack did not reliably return a structured instance for the complex schema. N5/N6 were activated; the final decision is **NO — SQLite FTS5 + embeddings**. See `spike/DECISION.md`.

## 1. Executive summary

Prečítal som nainštalovaný kód graphiti-core 0.29.2 (nie dokumentáciu) a spike adapter. Hlavné zistenia:

1. **Blocker pred final probe nebol driver, clone ani FalkorDB.** Vtedajší `Adapter.add_episode` posielal `graph.add_episode(..., uuid=episode_uuid)` s deterministicky generovaným **novým** UUID. Lenže v 0.29.2 parameter `uuid` znamená „**načítaj existujúcu epizódu** a preprocesuj ju" — kód je doslova `await EpisodicNode.get_by_uuid(self.driver, uuid) if uuid is not None else EpisodicNode(...)` ([graphiti.py:1102–1116](spike/.venv/lib/python3.11/site-packages/graphiti_core/graphiti.py)). Pre neexistujúcu epizódu je `NodeNotFoundError` **očakávané správanie API** a padlo by identicky aj na Neo4j.
2. **Predchádzajúce problémy (3–5) boli reálne**, kód ich potvrdzuje: `FalkorDriver.__init__` robí fire-and-forget `loop.create_task(build_indices_and_constraints())` (race) a `add_episode` pri `group_id != driver._database` mutuje `self.driver` aj `self.clients.driver`. Upstream FalkorDB model je: **1 group_id = 1 fyzický graf pomenovaný presne group_id, klient medzi nimi skáče mutáciou drivera.** Náš build-prefix koncept ide proti tomuto modelu — ale existuje podporovaná cesta, ktorá sa mu úplne vyhne (per-workspace klient, group_id nikdy ≠ `_database`).
3. **Rozsah patchovania treba triediť, nie paušalizovať.** Z šiestich „patchov" sú dva legitímne adaptéry (LLM fence wrapper; low-level CRUD je dokumentované API, nie obchádzka), jeden je nutný override reálneho upstream defektu (SequentialFalkorDriver bez constructor tasku), jeden je diagnostika, ktorá sa musí odstrániť (globálny monkeypatch `FalkorDriver.__init__`), a dva boli omyly z nesprávnej diagnózy (clone semantics iterácie) — vyvolané tým, že skutočná príčina bola `uuid` pasca.
4. **Odporúčanie: áno, jeden posledný, presne ohraničený probe** (odstrániť `uuid=`, per-workspace klient, žiadny clone) s tvrdou stop rule: **zmrazený patch budget** — ak probe vyžiada čo i len jeden ďalší zásah do graphiti-core interných tried, spike končí NO. Podľa čítania kódu má probe vysokú šancu prejsť; ak prejde, pokračuje sa v pôvodnom pláne 003 (benchmark, rebuild) — verdikt zostáva PENDING, nie GO.

Nie je to obhajoba Graphiti: keby aktuálne zlyhanie bolo naozaj v driver lifecycle, odporúčal by som NO ešte dnes. Je však zlé rozhodovať o NO na základe zlyhania, ktoré je našou chybou použitia API — a rovnako zlé je ignorovať, že cesta k tomuto zisteniu stála päť patchovacích iterácií, čo samo osebe je dôkaz o krehkosti knižnice (§6, §11).

## 2. What has passed

Potvrdzujem z `spike/` a `DECISION.md` (neoveroval som znovu behom, len konzistenciu artefaktov):

- Journal: SQLite schéma, event fold, `verify_state`, 5 unit testov, dataset 56 records / 24 queries, ingest s 1 exact-idempotency retry — **PASS**. Tieto výsledky sú backend-agnostické a platia aj pri SQLite fallbacku.
- Lokálny model: qwen3.6:35b-mlx + nomic-embed-text; fence-stripping wrapper (`LocalOpenAIGenericClient`) so strict validáciou; plain/JSON/Graphiti-level probes — **PASS**. Wrapper je legitímny adaptér (upstream `OpenAIGenericClient` cielene používa `/v1/chat/completions`, fence je vlastnosť modelu, nie Graphiti).
- **N1 explicitná invalidácia** — PASS (create → CRUD load → `invalid_at`/`expired_at` → save → reload → mimo current výsledkov). Zhoduje sa s kódom: `EntityEdge` má plné CRUD a temporal polia sú perzistované atribúty.
- **N2 základná izolácia** — PASS v checkpoint modeli (oddelené grafy, retry bez duplicity).
- Low-level CRUD vrátane build-prefixed clone probe — PASS, `pending_index_tasks=0`.

Poznámka ku klasifikácii: `explicit_triplet` cez `EntityNode/EntityEdge.save()` **nie je patch** — je to dokumentovaný CRUD povrch knižnice (rovnaký, aký používa upstream `add_triplet`). Do „patch účtu" ho nepočítam.

## 3. Failure chronology

| # | Problém | Skutočná povaha (po prečítaní kódu) |
|---|---|---|
| 1 | implicitný OpenAIClient vyžadoval cloud kľúč | konfiguračný default; vyriešené explicitným profilom — legitímne, žiadny patch |
| 2 | Qwen balil JSON do fence | vlastnosť modelu; local-only wrapper — legitímny adaptér |
| 3 | `FalkorDriver.__init__` → `loop.create_task(build_indices_and_constraints())` | **reálny upstream defekt** pre deterministické použitie ([falkordb_driver.py:176–184](spike/.venv/lib/python3.11/site-packages/graphiti_core/driver/falkordb_driver.py)): neb awaited task, plodí sa pri každom vytvorení drivera vrátane clonov; race s resetom/projekciou potvrdený dizajnom kódu. `SequentialFalkorDriver` (init bez tasku + `ensure_initialized`) je **odôvodnený override** |
| 4 | `add_episode(group_id=…)` klonuje driver; prvý fix (clone → vráť self) zlyhal | polovica omylu: mutácia je reálna (§4), ale `NodeNotFoundError` už vtedy takmer isto pochádzal z `uuid=` parametra (§5) — clone fix preto „nepomohol" |
| 5 | build-prefixed Sequential clone; low-level PASS, high-level stále padá | to isté: clone už vracal správnu triedu aj správny fyzický graf, ale `get_by_uuid(uuid=нové UUID)` musí padnúť vždy — driver je nevinný |

Kľúčová lekcia chronológie: **od problému 4 sa ladil nesprávny subsystém.** Chybová správa (`NodeNotFoundError` z `EpisodicNode.get_by_uuid`) ukazovala priamo na epizódu, nie na hranu/uzol projekcie — ale keďže prišla hneď po clone probléme, diagnóza sa zamkla na driver lifecycle. To nie je výčitka; je to presne ten typ nákladu, ktorý vzniká, keď knižnica kombinuje mutable stav, tiché API pasce a nedostatočné chybové hlášky (`node <uuid> not found` bez rozlíšenia „episode you asked to load doesn't exist").

## 4. Exact Graphiti 0.29.2 driver behavior

Fakty z kódu (súbory v `spike/.venv/.../graphiti_core/`):

1. **`add_episode` group_id logika** (graphiti.py:1074–1083):
   ```python
   if group_id is None:
       group_id = get_default_group_id(self.driver.provider)   # FalkorDB → '_'
   else:
       validate_group_id(group_id)
       if group_id != self.driver._database:
           self.driver = self.driver.clone(database=group_id)
           self.clients.driver = self.driver
   ```
   - `group_id=None` → **žiadny clone**, epizóda dostane `group_id='_'`, všetko beží na drivri, s ktorým bol klient vytvorený.
   - `group_id == driver._database` → **žiadny clone** (guard).
   - inak → **mutácia `self.driver` aj `self.clients.driver`** — klient natrvalo „preskočí" na iný graf; nie je to per-call kontext, je to zmena stavu inštancie. Rovnaký vzor je aj v `add_episode_bulk` (riadok 1309).
2. **`uuid` parameter je get-only** (graphiti.py:1102–1116): `uuid is not None` ⇒ `EpisodicNode.get_by_uuid(self.driver, uuid)` — určené na re-processing existujúcej epizódy. Nová epizóda sa vytvára konštruktorom **bez** možnosti dodať UUID; docstring („Optional uuid of the episode") túto asymetriu nijako nesignalizuje. Epizóda sa fyzicky ukladá až po extrakcii v `_process_episode_data` (riadok ~1170), čiže zlyhanie nastáva skôr, než sa čokoľvek zapíše.
3. **`FalkorDriver.clone`** (falkordb_driver.py:323–336): `database == self._database` → vráti **self**; `database == '_'` → nový driver s grafom `default_db`; inak nový `FalkorDriver(database=database)` — **group_id sa použije doslovne ako názov fyzického grafu**. Žiadny koncept prefixov, žiadne zdieľanie index-initializácie: každý nový driver si naplánuje vlastný background index task (bod 4).
4. **`FalkorDriver.__init__`** (176–184): `loop.create_task(self.build_indices_and_constraints())` — fire-and-forget, výnimky sa strácajú („Task exception was never retrieved"), a spúšťa sa pri každom clone na nový graf.
5. **Všetky queries idú na `self._database`** (`execute_query`, `_get_graph`): FalkorDB search nefanoutuje cez grafy — `search(group_ids=[…])` filtruje `group_id` property **vnútri jedného fyzického grafu**. Cross-workspace vyhľadávanie cez viac grafov je teda vždy náš fan-out (to už adapter robí správne).
6. **Odpoveď na otázku podporovaného modelu:** FalkorDB implementácia je fakticky postavená na **„jeden group_id = jeden fyzický graf = mutable driver hop"**. Model „jeden Graphiti klient, viac group_id" je na FalkorDB podporovaný len v zmysle sekvenčného preskakovania s mutáciou klienta — pre náš prípad (paralelné workspaces, deterministické build názvy) je bezpečný jedine vzor, kde **clone nikdy nenastane**: klient per workspace, `group_id=None` alebo `group_id == driver._database`.

## 5. Why high-level add_episode fails

Presná mechanika aktuálneho zlyhania:

1. `Adapter.add_episode` vygeneruje `episode_uuid = uuid5(build:episode:record_id)` (zámer: idempotencia podľa 003 §8.5),
2. vtedajší adapter zavolá `graph.add_episode(name='record:…', …, uuid=episode_uuid)` — **bez** `group_id`, takže clone vetva sa vôbec nevykoná,
3. Graphiti: `uuid is not None` ⇒ `EpisodicNode.get_by_uuid(driver, episode_uuid)`,
4. epizóda s týmto UUID neexistuje (je nová) ⇒ `NodeNotFoundError: node <uuid> not found`,
5. zlyhanie nastáva **pred akýmkoľvek zápisom** — preto low-level probes (ktoré `uuid=` nepoužívajú) prechádzajú a high-level „záhadne" padá na tom istom drivri a grafe.

Dôsledok pre dizajn 003 §8.5: **idempotenciu epizód nemožno riešiť dodaním UUID do `add_episode`.** Správny mechanizmus: nechať Graphiti vygenerovať UUID, uložiť ho do `projection_map`; idempotenciu drží journal kurzor; recovery po čiastočnom zápise = vyhľadanie epizód podľa `name = 'record:<record_id>'` v grafe workspace-u, zmazanie fragmentov, čistá re-projekcia. (Name-based cleanup je deterministický, lebo name je náš record_id.)

## 6. Is this a bug, unsupported use case, or wrong architecture?

Rozdelenie podľa zodpovednosti:

| Nález | Klasifikácia |
|---|---|
| `uuid=` pasca (get-only bez signalizácie, žiadne „create with uuid") | **naša chyba použitia + upstream API dizajnový defekt** (asymetria get/create v jednom parametri, zavádzajúci docstring, generická chybová hláška) |
| constructor `create_task` | **upstream defekt** pre akékoľvek deterministické použitie; override je nutný a odôvodnený |
| mutable `self.driver` pri group hope | **zámer upstream dizajnu**; nekompatibilný s naším pôvodným plánом „jeden klient + group_id fan-in", ale vyhnuteľný podporovaným vzorom (per-workspace klient) |
| build-prefixed názvy grafov | **naša architektonická požiadavka**, ktorú upstream nepozná; kompatibilná jedine ak prefix žije výhradne v `driver._database` a group_id sa do `add_episode` neposiela |
| diagnostický monkeypatch `fd.FalkorDriver.__init__` | náš dočasný nástroj; **musí sa odstrániť** (mutuje upstream globálne — presne to, čo sme si zakázali) |

Celkový verdikt: **nie je to „wrong architecture" na našej strane ani čistý bug na ich strane — je to knižnica s úzkym šťastným chodníkom a mutable jadrom, ktorá trestá každé vybočenie nezrozumiteľnou chybou.** To je legitímny, merateľný nález spiku: cena integrácie nie je v riadkoch nášho kódu (tie sú malé), ale v diagnostickej námahe pri každom prekvapení. Preto §11 zavádza patch budget ako tvrdé kritérium.

## 7. Per-workspace client option (Variant A)

Odpovede na položené otázky, priamo z kódu:

- **Dá sa `add_episode` zavolať bez group_id, aby sa clone nespustil?** Áno. `group_id=None` → default `'_'`, clone vetva sa nevykoná (§4 bod 1).
- **Je group_id povinný?** Nie. Pri vynechaní sa do nodes/edges uloží `group_id='_'` (FalkorDB default). Search potom musí používať `group_ids=['_']` — adapter to už robí.
- **Dá sa klient inicializovať priamo nad workspace driverom a používať stále rovnaký driver?** Áno — pokiaľ sa nikdy nepošle `group_id != driver._database`, `self.driver` sa nemení (guard). Adapterov before/after id check to navyše stráži fail-fast.
- **Dá sa group metadata zachovať bez fyzického clone?** Áno, dvoma spôsobmi: (a) `group_id=None` → `'_'`, workspace identita žije len v názve grafu (odporúčané — jednoduchšie), alebo (b) poslať `group_id` **rovný** `driver._database` (t. j. celý build-prefixed názov `spike_build_a__ai_pos`) — guard clone preskočí a nodes dostanú tento group_id. Variant (b) má výhodu, že group_id v dátach je samopopisný; nevýhodu, že build prefix presakuje do obsahu grafu (po rebuilde sa zmení). **Odporúčam (a).**
- **Je tento model podporovaný, alebo ďalší patch?** Je to podporovaná cesta (guard je upstream kód, nie náš zásah). Zostávajúce vlastné triedy: `LocalOpenAIGenericClient` (adaptér modelu) a `SequentialFalkorDriver` (init bez create_task + `ensure_initialized`). Clone override v SequentialFalkorDriver sa **mení z „mapuj na prefix" na fail-fast `raise`** — v tomto modeli clone nikdy nemá nastať, takže akékoľvek jeho zavolanie je bug, ktorý chceme počuť okamžite.

Praktické parametre variantu A:

- **Počet klientov:** ~8 workspace-ov × 1 klient; každý klient nesie referencie na LLM/embedder/reranker — tie sa dajú **zdieľať** (jedna inštancia klientov, viac Graphiti objektov), takže RAM navyše je zanedbateľná (Graphiti objekt je tenký; ťažké sú HTTP klienty a tie sú zdieľané).
- **Index initialization:** explicitne cez `ensure_initialized()` per driver — už implementované, deterministické.
- **Query fan-out:** už implementovaný (`Adapter.search` iteruje workspace klientov, `group_ids=['_']`). Merge + ranking naprieč workspace-mi je náš kód — bol v pláne od 002 (§9).
- **Rebuild A/B:** nový build prefix = nové fyzické grafy; staré sa zmažú droppnutím grafov. Čisté.
- **Upgrade riziko:** stredné — spoliehame sa na guard `group_id != self.driver._database` a na to, že `add_episode` bez group_id nezačne klonovať. Oboje treba pri každom upgrade overiť smoke testom (lacné: probe skript existuje). Pin verzie zostáva povinný.

## 8. Low-level Graphiti-only option (Variant B)

Použiť len `EntityNode/EpisodicNode/EntityEdge` CRUD + vlastné embeddings + vlastné retrieval queries znamená vzdať sa **jediných dvoch vecí, pre ktoré Graphiti vôbec máme**: extraction pipeline (extract → resolve → dedup → invalidate) a hotový hybridný search. Zostalo by: Pydantic typy a CRUD nad FalkorDB — čiže **vlastný systém na grafovej databáze s cudzími typmi**: niesli by sme súčasne údržbu grafovej DB, vlastného retrievalu *a* závislosť na graphiti-core triedach, ktoré sa menia. To je horšie ako oba ostatné varianty naraz. **Zamietam** — ak vypadne high-level API, správna odpoveď nie je low-level Graphiti, ale Variant C.

## 9. SQLite fallback option (Variant C)

Čo reálne stratíme: LLM extraction epizód (v 003 už aj tak zúžená a pre `fact` zvažovaná vypnúť), graph traversal / entity-centric queries (get_entity, susedia), Graphiti temporal edges (journal má vlastné supersede chains — **temporálna autorita je aj tak journal**), community search (nikdy sme nepotrebovali). Čo získame: jeden proces, nula patchov, deterministický rebuild triviálne, backup = kópia súboru, offline beh na MBP, žiadny upgrade risk tretej strany v kritickej ceste. Retrieval pre stovky records: FTS5 + sqlite-vec + typ/workspace/čas filtre — kvalitatívne dostačujúce (Q1 z 003 by sa meralo tým istým benchmarkom). Variant C zostáva plnohodnotný a **nie je to prehra** — presne pre tento prípad sa journal-first staval.

## 10. Complexity and maintenance comparison

| | A: per-workspace klienti | B: low-level only | C: SQLite fallback |
|---|---|---|---|
| Vlastné triedy nad cudzím interným API | 2 (LLM wrapper, SequentialFalkorDriver) | 2 + celý retrieval | 0 |
| Procesy/kontajnery | brain + FalkorDB | brain + FalkorDB | brain |
| Diagnostická náročnosť prekvapení | **vysoká** (dokázané touto chronológiou) | vysoká | nízka |
| Extraction + hybrid search | áno (hotové) | nie (vlastné) | nie (FTS+vec vlastné, ale malé) |
| Entity graph traversal | áno | čiastočne | nie (linky cez journal joins) |
| Rebuild determinizmus | dosiahnuteľný (N3 ešte nemerané) | dosiahnuteľný | triviálny |
| Upgrade riziko | stredné (pin + smoke) | vysoké | ~nulové |
| Týždenná údržba (odhad, osobný systém) | 0.5–1 h + neplánované špičky pri upgrade | najhoršia | <0.5 h |

## 11. Revised stop criteria

Pôvodné NO podmienky 003 (N1 invalidácia, N2 izolácia, N3 rebuild, N4 stabilita) zostávajú. Nález tohto review si žiada **nové kritérium N5 — patch budget**:

> **N5 (frozen patch budget).** Povolené vlastné zásahy do Graphiti integrácie sú odteraz zmrazené na: (a) `LocalOpenAIGenericClient` (fence strip + strict JSON validácia), (b) `SequentialFalkorDriver` obmedzený na: init bez constructor tasku, `ensure_initialized()`, fail-fast `clone()` (raise). Diagnostický monkeypatch `FalkorDriver.__init__` sa odstraňuje. **Ak úspešné dokončenie spiku (ingest, benchmark, rebuild) vyžiada čo i len jeden ďalší override, monkeypatch, fork alebo obídenie internej triedy/metódy graphiti-core, spike končí verdiktom NO bez ďalšej iterácie.**

A doplňujúca časová poistka:

> **N6 (timebox).** Finálny probe (§13) má timebox 0.5 dňa a jediný povolený fix je odstránenie `uuid=` + úprava idempotencie podľa §5. Ak probe neprejde na prvý korektný pokus, alebo odhalí ďalší interný lifecycle problém, spike = NO. Žiadna „ešte jedna hypotéza".

Zdôvodnenie N5: podmienka „tenká vlastná vrstva" bola v 002/003 explicitná. Päť iterácií patchovania nie je samo osebe NO (väčšina bola diagnostika jednej nesprávne lokalizovanej chyby), ale **šiesta by už bola** — v osobnom nízko-údržbovom systéme je knižnica, ktorú treba pravidelne premáhať, drahšia než vlastných 300 riadkov retrievalu.

## 12. Final recommendation

**Ani GO, ani NO — jeden posledný, presne ohraničený probe, a potom pokračovať v pláne 003 alebo skončiť.**

Odôvodnenie: rozhodnúť NO teraz by znamenalo zamietnuť Graphiti na základe zlyhania, ktoré je preukázateľne našou chybou použitia API (`uuid=`), pričom podporovaná integračná cesta (per-workspace klient bez clone) existuje a je z kódu overená. Rozhodnúť GO by bolo rovnako nepodložené: retrieval kvalita (G1–G16), rebuild (N3/N4) a prevádzka ostávajú nemerané a maintenance signál je reálne zlý (preto N5/N6). GO WITH CONDITIONS je predčasné — podmienky by sme formulovali pred dôkazom, že základný ingest vôbec beží.

## 13. Whether to run one final probe

**Áno — presne jeden, minimálny:**

1. jeden workspace (`probe`), jeden explicitný `SequentialFalkorDriver` (databáza `spike_probe_final__probe`), jeden Graphiti klient nad ním; **žiadny build root driver, žiadny reset cez zdieľaný klient**,
2. `add_episode` **bez `uuid=` a bez `group_id=`** (jediná zmena oproti dnešku; episode UUID si po návrate uložiť z `result.episode.uuid`),
3. save/read/search: epizóda existuje (`get_by_uuid` s vráteným UUID), search cez `group_ids=['_']` ju nájde,
4. invarianty: `id(graph.driver)` pred/po rovnaké, `driver._database` nezmenené, `pending_index_tasks == 0`, `ORIGINAL_FALKOR_DRIVER_CREATED` sa nikdy nevypíše (a následne sa diagnostický monkeypatch odstráni),
5. idempotenčný dodatok: druhé zavolanie projekcie toho istého recordu **bez** journal kurzora nesmie prebehnúť (kurzor blokuje); simulovaný partial-failure cleanup: query `MATCH (e:Episodic {name:'record:<id>'})` → počet epizód po recovery == 1,
6. mini-supersede: jeden `explicit_triplet` + N1-štýl invalidácia v tom istom grafe (overenie, že low-level a high-level artefakty koexistujú v jednom workspace grafe).

Nič viac — žiadne frameworkové zmeny, žiadne úpravy datasетu, žiadny benchmark v tomto kroku. Ak probe prejde, pokračuje pôvodný plán 003 (plný ingest na mini-core, 24 queries, rebuild) pod N5/N6. Ak neprejde → NO a Variant C.

## 14. Required changes to Proposal 002/003

Zatiaľ nevykonať — navrhované znenie:

**Proposal 002** (malé doplnenie, architektúra sa nemení):
- §12 (Graphiti assessment) doplniť: *„Overené z kódu 0.29.2: FalkorDB backend viaže group_id na fyzický graf a `add_episode` mení driver inštancie pri group hope. Podporovaný integračný tvar pre Pavol-Brain je jeden Graphiti klient na workspace nad vlastným grafom, `group_id` sa do add_episode neposiela; cross-workspace search je fan-out v našej vrstve (čo bolo aj tak v pláne §9)."*
- §21 (Recommended MVP) doplniť do rizík: patch budget princíp (odkaz na 004 §11).

**Proposal 003**:
- §8.5 (idempotencia) **opraviť**: zrušiť „`add_memory`/`add_episode` s vlastným uuid"; nahradiť: *„Episode UUID generuje Graphiti; projekčná idempotencia = journal kurzor (`projection_map`); recovery po čiastočnom zápise = name-based cleanup (`record:<record_id>`) + re-projekcia. `uuid` parameter add_episode je get-only (re-processing) a nesmie sa používať pri vytváraní."*
- §22 doplniť **N5 (frozen patch budget)** a **N6 (probe timebox)** v znení z §11 tohto dokumentu.
- §21 (integration seam) doplniť poznámku: model „jeden klient + group_id fan-in" je na FalkorDB nepodporovateľný; platí „klient per workspace".
- Checkpoint výsledky: N1 = PASS, N2 = PASS (s odkazom na results).

**spike/DECISION.md** — navrhované interim znenie (verdikt zostáva PENDING):

> **Verdikt: PENDING — architecture review 004 vykonaný; čaká final probe.**
> Doterajšie zlyhanie high-level ingestu je vysvetlené: `add_episode(uuid=…)` je get-only API (re-processing existujúcej epizódy); NodeNotFoundError bol očakávaným dôsledkom nášho volania, nie driver defektом. Driver nálezy (constructor index task, mutable driver pri group hope, group=fyzický graf) sú potvrdené z kódu a determinujú integračný tvar: klient per workspace, group_id sa neposiela, clone je fail-fast zakázaný. Final probe podľa 004 §13 pod stop rules N5/N6: pass → pokračuje plný benchmark a rebuild; fail → NO a SQLite FTS5+embeddings fallback. Diagnostický monkeypatch FalkorDriver.__init__ bude odstránený pred probe.

## 15. Suggested DECISION.md wording

Pozri §14 — znenie je tam uvedené v celku, aby sa dalo prevziať doslova. Po final probe sa dopĺňa jedna z vetiev:

- **pass:** „Final probe PASS (dátum, results ref): add_episode bez uuid/group_id, driver stabilný, 0 pending tasks, idempotencia cez kurzor + name-based recovery, koexistencia explicit tripletov a epizód. Pokračuje sa plným ingest/benchmark/rebuild podľa 003; N5/N6 v platnosti."
- **fail:** „Final probe FAIL (dátum, results ref, presná chyba): Graphiti = NO podľa N5/N6. Aktivuje sa Variant C — SQLite FTS5 + embeddings; agentné API a journal sa nemenia. Graphiti nálezy archivované pre prípadné budúce prehodnotenie pri major verzii."

## 16. Next step

1. Schváliť tento review a stop rules N5/N6.
2. Vykonať final probe podľa §13 (timebox 0.5 dňa, jediný fix: odstrániť `uuid=`; odstrániť diagnostický monkeypatch; clone → fail-fast).
3. Zapísať výsledok do DECISION.md podľa §15 a podľa vetvy buď spustiť plný benchmark+rebuild (003), alebo prepnúť na Variant C.
4. Po ukončení spiku premietnuť §14 zmeny do 002/003.

**Čo teraz nerobiť:** žiadne ďalšie úpravy SequentialFalkorDriver clone semantics, žiadne nové wrappery, žiadny pokus o „jeden klient + group_id", žiadne úpravy 002/003 pred výsledkom probe, žiadny commit.

---

## Prílohy — čítané zdroje

- `spike/.venv/.../graphiti_core/graphiti.py` — add_episode (riadky 980–1230): group_id/clone guard (1074–1083), uuid get-only (1102–1116), episode save až v `_process_episode_data` (1170)
- `spike/.venv/.../graphiti_core/driver/falkordb_driver.py` — constructor create_task (176–184), clone semantics (323–336), `_get_graph`/`execute_query` viazané na `_database`
- `spike/.venv/.../graphiti_core/helpers.py` — `get_default_group_id` (FalkorDB → `'_'`)
- `spike/.venv/.../graphiti_core/driver/falkordb/operations/episode_node_ops.py` — `NodeNotFoundError` (151)
- `spike/src/graphiti_adapter.py` — `Adapter.add_episode` (uuid=episode_uuid, bez group_id), fan-out search, workspace klienti
- `spike/src/sequential_falkor.py` — SequentialFalkorDriver, diagnostický monkeypatch (na odstránenie)
- `spike/scripts/driver_probe.py`, `spike/DECISION.md`, `spike/results/*`
- [Proposal 002](002-pavol-brain-shared-memory-and-knowledge-graph.md) · [Proposal 003](003-graphiti-spike-design.md)
