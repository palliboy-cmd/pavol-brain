# Proposal 007: Decision Audit, External Alternatives, and Devil's Advocate Review

- **Status:** Analysis only — nothing implemented, nothing decided
- **Dátum:** 2026-07-12
- **Autor:** Claude (Fable 5) na základe zadania Pavla Pavlovského
- **Vstupy:** Proposals 001–006 · `spike/DECISION.md` · `sqlite-spike/DECISION.md` · web rešerš (overené 2026-07-12, zdroje v §8)
- **Účel:** kriticky preveriť doterajšie rozhodnutia proti externým alternatívam a explicitne sformulovať najsilnejšie protiargumenty (diablov advokát), skôr než sa začne implementovať Slice 1 z Proposal 006.

---

## 1. Metóda a poctivosť analýzy

Tri druhy tvrdení v tomto dokumente, vždy označené:

- **[repo]** — fakty z lokálnych dokumentov a spike výsledkov; overiteľné v tomto repozitári.
- **[web]** — tvrdenia z internetovej rešerše k 2026-07-12; sekundárne zdroje, môžu byť marketingové, treba ich brať s rezervou.
- **[úsudok]** — moja interpretácia; môže byť mylná a je označená, aby sa nedala zameniť za fakt.

Diablov advokát v §5 je písaný vážne: každý protiargument je formulovaný tak silno, ako sa dá, a až potom nasleduje obhajoba alebo priznanie, že obhajoba neexistuje.

## 2. Inventár doterajších rozhodnutí

| # | Rozhodnutie | Kde | Stav |
|---|---|---|---|
| D1 | Primárny problém je fragmentovaná pamäť agentov, nie gateway k aplikáciám | P002 (koriguje P001) | prijaté |
| D2 | Kanonická pravda = append-only SQLite journal (immutable `memory_records` + `memory_events` + fold do `record_state`); retrieval index je vždy odvodený a rebuildovateľný | P002, P003 | prijaté, opakovane potvrdené |
| D3 | Write policy / review-first: candidate → accepted, explicit supersede, žiadny nekontrolovaný zápis agentov | P002 | prijaté |
| D4 | Graphiti ako retrieval backend: **NO for the tested local Graphiti 0.29.2 + structured-output stack; graph retrieval quality was not evaluated.** | P003, P004, `spike/DECISION.md` | uzavreté |
| D5 | Fallback backend: SQLite FTS5 + lokálne embeddings, deterministický ranking | `spike/DECISION.md`, P005 | prijaté |
| D6 | Benchmark preflight: oprava 20/24 nekonzistentných queries pred meraním; pôvodný manifest immutable | P005 §4 | vykonané |
| D7 | Výsledný verdikt: **GO WITH CONDITIONS — vector-only**; FTS-only neprešiel (top-3 70,83 %), hybrid RRF prešiel ale neselektovaný (95,83 % < 100 %); S4 noise FAIL 10,53 % | `sqlite-spike/DECISION.md` | uzavreté s podmienkami |
| D8 | Podmienky: rozšíriť benchmark, znovu merať noise na nezávislej sade, žiadny tuning na 24 queries, provenance povinná, retrieval bez mutácií | `sqlite-spike/DECISION.md` | otvorené (blokujú „production" label) |
| D9 | Integračná vrstva: `brain.*` kontrakt, Python library first + MCP adaptér v Slice 4; fail-closed pri výpadku embeddings; mini-core single host, žiadny multi-master | P006 | draft, neimplementované |
| D10 | Embedding model: `nomic-embed-text:latest`, zafixovaný fingerprintom | P005, P006 | implicitne prijaté — **nikdy nebolo samostatne rozhodované** |

Poznámka k D10: je to jediné rozhodnutie v zozname, ktoré nevzniklo vedomým porovnaním alternatív — model bol zdedený z Graphiti spiku, kde bol zvolený ako „čo beží lokálne v Ollame". [úsudok] To z neho robí najslabšie ukotvené rozhodnutie celej série.

## 3. Konfrontácia s externými alternatívami

### 3.1 Hotové memory systémy (Mem0, Letta, Zep, Cognee, CORE, Basic Memory)

Stav ekosystému 2026 [web]: porovnania sa zhodujú, že žiadny systém nevyhráva plošne — Mem0 pre jednoduchú chatbot pamäť, Zep/Graphiti pre temporálne reasoning (LongMemEval ~63,8 % vs Mem0 49 %), Letta pre dlhožijúce autonómne agenty, Cognee pre graph-RAG nad dokumentmi. Basic Memory je lokálny Markdown+SQLite MCP server s hybrid FTS+vector search, blízky duchom (local-first, vlastníctvo dát), ale bez review-first write policy, bez append-only auditovateľného journalu a bez workspace/sensitivity izolácie ako tvrdého invariantu.

**Záver:** nič z toho neposkytuje kombináciu *append-only kanonická pravda + review-first write policy + workspace/sensitivity hard isolation + deterministický rebuildovateľný index*. D2/D3 zostávajú správne. Najbližší konkurent celkovej architektúry je Basic Memory — a keby projekt začínal dnes od nuly bez požiadavky na review-first autoritu, bol by legitímnou skratkou. S touto požiadavkou nie je. [úsudok]

### 3.2 Vector storage (ručné BLOB + exact cosine vs. sqlite-vec vs. embedded vector DB)

- **sqlite-vec (asg017)** [web]: brute-force KNN priamo v SQLite, beží všade, SIMD. Ale v repo visí otázka údržby — issue #226 „This project hasn't been updated for half a year. Is it no longer being maintained?" (jún 2025); releasy sa odvtedy objavili, no kadencia je pomalá. Alternatívny fork/konkurent `sqliteai/sqlite-vector` má komerčný licenčný model pre produkčné použitie.
- **LanceDB / Chroma** [web]: embedded DB pre milióny vektorov, ANN, vlastný storage formát. Pri korpuse 51 dokumentov je to kanón na vrabca a druhý dátový systém na údržbu.
- **Súčasný prístup** [repo]: vektory ako BLOB v `retrieval.db`, exact cosine v Pythone, p95 < 31 ms.

**Záver:** pri desiatkach až tisícoch records je exact cosine bez extension správne — žiadna závislosť, žiadne licenčné riziko, deterministické. sqlite-vec sa stáva relevantným až keď p95 reálne degraduje (rádovo ≥ 50–100k vektorov pri 768 dim [úsudok]), a vtedy treba znovu preveriť jeho maintenance stav. P006 §3 „no ANN in MVP" je externe potvrdené ako správne.

### 3.3 Hybrid retrieval literatúra

[web] Literatúra a produkčné guides sa výrazne zhodujú: hybrid BM25+vector s RRF typicky **poráža** vector-only — na WANDS benchmarku hybrid NDCG 0,7497 vs ~0,695 pre obe čisté routes; iné merania uvádzajú recall@10 91 % hybrid vs 78 % dense-only; BM25 vyhráva na exact-match doménovom žargóne, identifikátoroch a vlastných menách, ktoré sú slabo zastúpené v embedding tréningu.

Toto je v priamom napätí s D7 (hybrid disabled) — rozobraté ako DA-1 v §5.

### 3.4 Embedding modely

[web] Stav 2026 pre lokálne multilingválne embeddings: **BGE-M3** (100+ jazykov, dense+sparse+multi-vector v jednom modeli) a **Qwen3-Embedding** (MTEB multilingual leader, voliteľné dimenzie 256–2048) sú považované za výrazne silnejšie multilingválne voľby než nomic-embed-text v1; existuje aj Nomic Embed V2. `nomic-embed-text` v1 je primárne anglocentrický model.

Korpus a queries Pavol-Brain sú zmes slovenčiny a angličtiny [repo — P001–P004 sú po slovensky, records obsahujú slovenské formulácie]. Toto je pravdepodobne najlacnejšia nevyskúšaná páka na S4 noise — rozobraté ako DA-3.

## 4. Čo externá rešerš potvrdila

1. **Journal-first, index-derived** (D2) — všeobecne uznávaný pattern; ani jeden z porovnávaných memory systémov neponúka lepšiu auditovateľnosť. Rozhodnutie stojí.
2. **NO for the tested local Graphiti 0.29.2 + structured-output stack; graph retrieval quality was not evaluated.** Nezávislé porovnania len orientačne naznačujú, že Zep/Graphiti je silné v temporálnom reasoningu, ale celý ekosystém predpokladá spoľahlivý structured-output LLM; lokálny stack ho nedodal a privacy constraint trvá. Pozri DA-9.
3. **Bez ANN, bez vector DB** (P006 §3) — pri tejto veľkosti korpusu jednoznačne správne; exact cosine je zároveň jediná plne deterministická voľba.
4. **Vlastný tenký layer namiesto hotového memory frameworku** (D1–D3) — potvrdené; hotové systémy nespĺňajú review-first a isolation požiadavky.
5. **MCP ako agent-facing forma** (D9) — ekosystém sa v 2026 zbehol na MCP ako štandard pre agent tooling; voľba adaptéra je nekontroverzná.

## 5. Diablov advokát

### DA-1: „Vyradili ste hybrid na základe jedného query."

**Argument:** Vector-only top-3 100 % vs hybrid 95,83 % je rozdiel **presne jedného query z 24** (24/24 vs 23/24, regres Q21) [repo]. McNemarov test s jedným diskordantným párom nedáva žiadnu signifikanciu — p ≈ 1,0 [úsudok, elementárna štatistika]. Externé benchmarky pritom systematicky ukazujú opak: hybrid > vector-only, najmä na exact-match žargóne [web, §3.3]. „Hybrid disabled" je teda silné rozhodnutie postavené na štatisticky bezcennom rozdiele — a v smere, ktorý je proti literatúre. Korpus s 51 dokumentmi navyše hybrid znevýhodňuje: BM25 exceluje na identifikátoroch a rare terms, ktorých diskriminačná sila rastie s veľkosťou korpusu. Pri 5 000 records môže byť poradie routes opačné.

**Obhajoba:** Rozhodnutie nebolo „hybrid je horší navždy", ale „vector-only je jednoduchší a na dnešných dátach nie horší" — menej pohyblivých častí, žiadne RRF váhy na tuning, čo priamo podporuje podmienku „no tuning". [repo — DECISION formulácia „not preferred", nie „failed"]

**Verdikt:** Obhajoba drží pre MVP, ale **nie pre trvalé vyradenie**. Odporúčanie R2: hybrid nechať ako *benchmark-only* route a rozhodnutie automaticky znovu otvoriť pri Slice 5 na rozšírenej sade. To nie je hybrid tuning — je to poctivé meranie už existujúcej route na nových dátach.

### DA-2: „S4 noise FAIL je rovnako bezcenný ako hybrid rozdiel — a vy ste si z neho vybrali, čo sa vám hodí."

**Argument:** S4 noise 6/57 = 10,53 % vs prah ≤ 10 % — to je **jeden výsledok nad prahom** (5/57 = 8,77 % by prešlo) [repo]. 95 % interval spoľahlivosti pre 6/57 je zhruba 2,6–18,5 % [úsudok, binomický CI]. Ak je rozdiel 23/24 vs 24/24 „štatisticky bezcenný" (DA-1), potom aj tento FAIL je bezcenný — nemôžete jedno číslo použiť na vyradenie hybridu a druhé rovnako krehké číslo vyhlásiť za dôvod podmienok. A symetricky: top-3 100 % na 24 queries znamená len toľko, že skutočná úspešnosť je s 95 % istotou niekde nad ~87,5 % (rule of three). Celý verdikt stojí na vzorkách, ktoré nič nedokazujú.

**Obhajoba:** Presne toto podmienky riešia — verdikt je GO **WITH CONDITIONS** a nie GO práve preto, že vzorka je malá; podmienka 4 explicitne žiada nezávislú sadu [repo]. Nekonzistencia z argumentu je reálna, ale vyriešená konzervatívne: krehký pozitívny signál (100 %) nedostal production label a krehký negatívny signál (10,53 %) sa musí re-merať.

**Verdikt:** Obhajoba drží, ale s dôsledkom pre budúcnosť — **gates bez definovanej minimálnej vzorky sú divadlo**. Odporúčanie R6: Slice 5 musí mať vopred určené n (≥ 100 queries) a pravidlo, že gate padá len ak celý CI leží nad prahom.

### DA-3: „Meriate noise anglocentrickým modelom na slovensko-anglickom korpuse a čudujete sa šumu."

**Argument:** `nomic-embed-text` v1 je anglocentrický model [web]; korpus a reálne queries sú SK/EN zmes [repo]. Multilingválne modely (BGE-M3, Qwen3-Embedding) sú v 2026 považované za výrazne silnejšie [web, §3.4]. Je dosť možné, že S4 noise 10,53 % nie je vlastnosť „vector-only route", ale vlastnosť slabého modelu na tomto jazyku — a celá kaskáda podmienok rieši symptóm. Model nebol nikdy vyberaný (D10), len zdedený. Najlacnejší možný experiment — vymeniť model a prebuildovať index (architektúra to explicitne umožňuje: embedding cache keyed by model fingerprint, rebuild z journalu) — sa nikdy nespravil.

**Obhajoba:** Čiastočná: A/B modelov na *súčasných* 24 queries by porušilo podmienku „no tuning on the 24" v duchu, ak by sa model vyberal podľa nich. [úsudok]

**Verdikt:** Obhajoba je slabá — podmienka zakazuje tuning *na starej sade*, nie výber modelu na *novej nezávislej* sade. **Toto je najsilnejší nález celého auditu:** model bake-off (nomic v1 vs Nomic V2 vs BGE-M3 vs Qwen3-Embedding) na rozšírenom benchmarku zo Slice 5 je vysoká páka, nulové architektonické riziko (fingerprint + rebuild je na to navrhnutý) a mal by sa stať súčasťou Slice 5, nie odloženou úvahou. Pozri R1.

### DA-4: „Staviate a udržiavate vlastný memory systém, ktorý svet už napísal."

**Argument:** Journal, fold, projekcia, embeddings, buildy, MCP server, audit, benchmark harness — to všetko je kód, ktorý bude Pavol sám udržiavať popri práci. Mem0, Zep, Letta, Basic Memory majú tímy a komunity. P001 sám identifikoval ako riziko č. 1 „systém, ktorého údržba stojí viac času, než šetrí" [repo]. Vlastný systém je presne ten scenár.

**Obhajoba:** Rešerš §3.1 — žiadny hotový systém nespĺňa review-first + append-only autoritu + hard isolation; prevzatie cudzieho systému by tieto požiadavky zrušilo, nie splnilo. Navyše zvolený scope je zámerne malý (SQLite, žiadny server framework, exact cosine — žiadna infra) a doterajšie spiky ukázali disciplínu ukončiť nekompatibilný smer: **NO for the tested local Graphiti 0.29.2 + structured-output stack; graph retrieval quality was not evaluated.** [repo]

**Verdikt:** Obhajoba drží, **pokiaľ platí disciplína malého scope**. Riziko sa nemeria dnes, ale pri Slice 4+ — ak MCP adaptér začne bobtnať (auth, remote access, dashboard), argument diablovho advokáta ožíva. Kill kritériá z P001/P002 (merané použitie po 4 týždňoch) treba preniesť aj na retrieval vrstvu; P006 ich nespomína.

### DA-5: „Fail-closed pri výpadku embeddings znamená, že mozog zdochne presne vtedy, keď ho potrebujete."

**Argument:** Celý query path závisí od jedného lokálneho Ollama procesu. P006 §19 volí fail-closed default — pri výpadku endpointu search nefunguje. Jediný fallback (FTS) je za explicitným flagom a sám nesplnil top-3 gate. Výsledok: single point of failure bez automatickej degradácie, na osobnej infra (mini-core), kde procesy padajú.

**Obhajoba:** Fail-closed je vedomá voľba proti *tichej* degradácii — FTS s top-3 70,83 % by potichu vracal horšie výsledky a agent by to nevedel [repo]. Degraded route existuje, len je labeled a opt-in. Pri osobnom systéme je „zlyhaj nahlas, reštartni Ollamu" lepšie než „nenápadne odpovedaj horšie".

**Verdikt:** Obhajoba drží pre správnosť, nie pre dostupnosť. Chýba lacná prevádzková mitigácia: watchdog/auto-restart embedding endpointu + health probe v `brain.health()` (už navrhnutý) + voliteľný **query-embedding cache** pre opakované queries [úsudok — cache je deterministická, neporušuje žiadnu podmienku]. Pozri R5.

### DA-6: „ChatGPT integrácia je fikcia a všetci to vedia."

**Argument:** Cieľ P006 §15 menuje ChatGPT ako konzumenta, ale mini-core je LAN/VPN-only a P001 už v 2026-07-10 identifikoval, že ChatGPT vyžaduje verejný HTTPS endpoint [repo]. P006 to rieši vetou „until ChatGPT can reach mini-core, it simply has no brain access" — čo je elegantné priznanie, že štvrtina menovaných agentov integrovaná nebude. „Jeden mozog pre všetkých agentov" tak v skutočnosti znamená „pre agentov, ktorí bežia doma".

**Obhajoba:** Poctivá odpoveď je čiastočné priznanie: áno, ChatGPT je mimo MVP a žiadna lacná bezpečná cesta neexistuje (verejný endpoint = auth, TLS, expozícia sensitive dát — presne to, čo §18 zakazuje v tejto fáze). Alternatíva „postaviť to hneď" by bola horšia chyba.

**Verdikt:** Priznať explicitne, nie implicitne. Dokumenty by mali prestať menovať ChatGPT ako MVP konzumenta a presunúť ho do fázy s vlastným rozhodnutím (tunnel + per-agent token + non-sensitive workspaces only [úsudok]). Inak vzniká falošné očakávanie, že sa to „nejako doladí".

### DA-7: „Library-first je pohodlie autora, nie architektúra."

**Argument:** Hermes, Claude aj Codex hovoria MCP natívne. Jediný konzument Python library je... zatiaľ nikto konkrétny. Kontrakt definovaný ako Python signatúry sa bude aj tak musieť previesť do MCP tool schém — a tie sa stanú skutočným kontraktom, lebo cez ne pôjde všetka prevádzka. Slice 1 (library) tak odkladá jediný integračný krok, ktorý agentom reálne niečo dá, a riskuje, že library API a MCP schéma sa rozídu.

**Obhajoba:** Library nie je transport, je to implementácia sémantiky (validácia, ranking, policy, determinizmus) — a tú treba otestovať proti baseline pred akýmkoľvek transportom. MCP server bez otestovanej sémantiky je len rýchlejšia cesta k zlým odpovediam. Poradie slice-ov je test-first, nie authorship-comfort. [úsudok]

**Verdikt:** Obhajoba drží pod jednou podmienkou: MCP tool schémy sa **zafixujú už v Slice 1** ako súčasť kontraktu (JSON schémy request/response z P006 §5–6 sú de facto hotové), aby library a adaptér nemohli divergovať. Slice 4 potom len mapuje, nerozhoduje.

### DA-8: „Benchmark s 51 dokumentmi nemeria nič, čo bude platiť o rok."

**Argument:** Pri 51 dokumentoch je top-3 hit takmer zaručený pre akúkoľvek nenáhodnú metódu — priestor kandidátov je maličký. Latencia p95 < 31 ms pri exact cosine nad 51 vektormi je triviálna a nič nehovorí o 10 000 records. Zero leaks pri 51 records nedokazuje isolation pri zložitejších scope kombináciách. Systém prešiel skúškou, ktorú nemohol nespraviť.

**Obhajoba:** Čiastočná: FTS-only na tom istom „triviálnom" korpuse **neprešiel** (70,83 %), takže benchmark diskriminačnú silu preukázateľne má [repo]. Leaks sa testovali aj adversariálne (S-testy). Ale škálovacia námietka je nevyvrátiteľná — nič v spiku nemeria rast.

**Verdikt:** Prijať čiastočne. Slice 5 rozšírený benchmark musí okrem nových queries obsahovať aj **syntetické zväčšenie korpusu** (rádovo 10× distractor records), inak sa noise a latencia znovu zmerajú v skleníku. [úsudok]

### DA-9: „NO for the tested local Graphiti 0.29.2 + structured-output stack; graph retrieval quality was not evaluated."

**Argument:** Spike padol na tom, že *konkrétny lokálny inference server* nevedel structured output pre jednu zložitú Pydantic schému [repo — `spike/DECISION.md`: retrieval benchmark a rebuild sú „NOT EVALUATED, not failed"]. Kvalita grafového retrievalu sa nikdy nemerala. Medzitým externé merania ukazujú Zep/Graphiti ako lídra temporálneho reasoningu [web]. Zavrhli ste smer kvôli chybe infraštruktúry a teraz sa NO cituje, akoby zlyhal koncept.

**Obhajoba:** DECISION.md je v tomto disciplinovaný — explicitne rozlišuje failed vs NOT EVALUATED a necháva re-open klauzulu („significant new version or demonstrably reliable local structured-output stack") [repo]. Dôvod NO nebola jedna chyba, ale *cena spoľahlivosti*: päť patch iterácií za jeden deň na krehkom rozhraní, s privacy constraintom, ktorý cloud modely (ktoré by problém vyriešili) vylučuje pre sensitive workspaces. A P002 od začiatku hovorí, že graf smie byť len odvodený index — čiže nič nenávratné sa nestalo: journal umožňuje graf kedykoľvek doplniť ako ďalšiu projekciu popri vektoroch, nie namiesto nich.

**Verdikt:** Obhajoba drží. V budúcich dokumentoch treba používať presnú formuláciu **„NO for the tested local Graphiti 0.29.2 + structured-output stack; graph retrieval quality was not evaluated."**, aby sa z neho nestala falošná inštitucionálna pamäť.

### DA-10: „Podmienka `no tuning' je performatívna — min_score, prahy aj thresholdy si aj tak raz niekto nastaví podľa toho, čo vidí."

**Argument:** P006 pripúšťa `min_score` v scheme „bez defaultu". Ale prvý používateľ, ktorému príde šum, si nastaví min_score podľa výsledkov, ktoré vidí — a tie pochádzajú z korpusu, na ktorom sa ladiť nesmie. Zákaz tuningu bez enforcementu je len veta v dokumente.

**Obhajoba:** Enforcement existuje čiastočne: audit log zaznamenáva filtre vrátane min_score, takže tuning by bol aspoň viditeľný [repo — P006 §17]. Úplný technický enforcement nie je možný ani zmysluplný pri osobnom systéme.

**Verdikt:** Prijať s malou zmenou: kým Slice 5 neprebehne, `min_score` **odmietať** (validation error „not enabled pre-Slice-5"), nie len nedefaultovať. Jedna riadková zmena v návrhu validácie, ktorá robí podmienku reálnou. Pozri R4.

## 6. Návrh lepšieho riešenia — konkrétne korekcie

Žiadne z jadrových rozhodnutí (D1–D5, D9 topológia) netreba zvrátiť. „Lepšie riešenie" nie je iný backend — je to sedem korekcií existujúceho plánu:

- **R1 — Embedding model bake-off v Slice 5 (najvyššia priorita).** Rozšírený benchmark spustiť proti nomic-embed-text v1 (baseline), Nomic Embed V2, BGE-M3 a Qwen3-Embedding (veľkosť podľa RAM mini-core). Výber modelu na *novej* sade neporušuje žiadnu podmienku; architektúra (model fingerprint, embedding cache, rebuild z journalu) je na výmenu modelu explicitne stavaná. Rieši pravdepodobnú koreňovú príčinu S4 noise (DA-3).
- **R2 — Hybrid nechať ako benchmark-only route.** Nezmazať RRF kód, držať ho mimo produkčného API, a v Slice 5 ho automaticky re-merať na rozšírenej sade spolu s R1. Rozhodnutie „hybrid disabled" sa tým mení z trvalého na „disabled, re-evaluated at Slice 5" (DA-1). Žiadny tuning váh pred Slice 5.
- **R3 — Slice 5 benchmark musí obsahovať: (a) ≥ 100 nových queries s vopred určeným podielom slovenských, žargónových a identifier-based queries, (b) ~10× distractor korpus, (c) gates definované s minimálnou vzorkou a CI pravidlom** (gate padá len ak celý interval leží za prahom) (DA-2, DA-8).
- **R4 — `min_score` do Slice 5 tvrdo odmietať** validáciou, nie konvenciou (DA-10).
- **R5 — Prevádzková mitigácia embedding SPOF:** watchdog/auto-restart Ollamy na mini-core + deterministický query-embedding cache (key: normalized query + model fingerprint). Fail-closed default zostáva (DA-5).
- **R6 — MCP tool schémy zafixovať už v Slice 1** ako súčasť kontraktu; Slice 4 ich len implementuje (DA-7).
- **R7 — ChatGPT explicitne vyňať z MVP scope** vo všetkých dokumentoch; prípadný remote prístup je samostatné budúce rozhodnutie s vlastným security návrhom (DA-6). Zároveň preniesť kill kritériá z P001/P002 na retrieval vrstvu: ak po 4 týždňoch reálneho používania Slice 1–4 nebude merateľné denné použitie, projekt sa zastavuje rovnako nemilosrdne ako Graphiti (DA-4).

## 7. Čo stojí a čo sa mení — súhrn

| Rozhodnutie | Audit verdikt |
|---|---|
| Journal-first, append-only kanonická pravda (D2) | **Stojí** — externe potvrdené, žiadna alternatíva nespĺňa požiadavky |
| Review-first write policy (D3) | **Stojí** |
| Graphiti (D4) | **NO for the tested local Graphiti 0.29.2 + structured-output stack; graph retrieval quality was not evaluated.** |
| SQLite + exact cosine, bez ANN, bez vector DB (D5, P006 §3) | **Stojí** — pri tejto veľkosti korpusu jednoznačne |
| Vector-only ako MVP route (D7) | **Stojí pre MVP**, ale hybrid re-merať v Slice 5 (R2) — trvalé vyradenie nie je podložené |
| Hybrid disabled navždy | **Mení sa** na „disabled until Slice 5 re-measurement" |
| nomic-embed-text ako model (D10) | **Najslabšie rozhodnutie série** — nikdy nebolo vedome prijaté; bake-off v Slice 5 (R1) |
| S4 noise FAIL ⇒ podmienky (D8) | **Stojí**, ale budúce gates potrebujú minimálnu vzorku a CI pravidlo (R3) |
| Library-first, MCP v Slice 4 (D9) | **Stojí** s podmienkou zafixovania MCP schém v Slice 1 (R6) |
| Fail-closed pri embedding outage (D9) | **Stojí** + prevádzková mitigácia (R5) |
| ChatGPT ako MVP konzument | **Mení sa** — explicitne mimo MVP (R7) |

Najdôležitejšia jedna veta auditu: **problém pravdepodobne nie je v routes (vector vs hybrid), ale v modeli — jediné rozhodnutie, ktoré nikdy nebolo vedome prijaté, je zároveň to, ktoré najpravdepodobnejšie spôsobilo jediný FAIL benchmarku.**

## 8. Zdroje (web, overené 2026-07-12)

- [sqlite-vec (asg017)](https://github.com/asg017/sqlite-vec) · [maintenance issue #226](https://github.com/asg017/sqlite-vec/issues/226) · [releases](https://github.com/asg017/sqlite-vec/releases) · [sqliteai/sqlite-vector](https://github.com/sqliteai/sqlite-vector) · [The State of Vector Search in SQLite](https://marcobambini.substack.com/p/the-state-of-vector-search-in-sqlite)
- [AI Agent Memory 2026 — Mem0, Zep, Graphiti, Letta, LangMem](https://medium.com/@wasowski.jarek/i-compared-5-ai-agent-memory-systems-across-6-dimensions-none-wins-6a658335ed0a) · [Comparison of AI Agent Memory Systems 2026](https://explore.n1n.ai/blog/ai-agent-memory-comparison-2026-mem0-zep-letta-cognee-2026-04-23) · [Agent Memory Frameworks Tested](https://particula.tech/blog/agent-memory-frameworks-tested-mem0-zep-letta-cognee-2026) · [Best AI Agent Memory Systems](https://vectorize.io/articles/best-ai-agent-memory-systems)
- [Basic Memory MCP](https://mcpservers.org/servers/basicmachines-co/basic-memory) · [Basic Memory overview](https://mcpmarket.com/server/basic-memory)
- [Hybrid Search for RAG: BM25 + dense (2026)](https://denser.ai/blog/hybrid-search-for-rag/) · [BM25 vs Vector Embeddings](https://docs.bswen.com/blog/2026-03-27-bm25-vs-vector-embeddings/) · [Hybrid Search Guide (supermemory)](https://supermemory.ai/blog/hybrid-search-guide/) · [Hybrid search reference 2026](https://www.digitalapplied.com/blog/hybrid-search-bm25-vector-reranking-reference-2026)
- [Best Ollama Embedding Models 2026 (MTEB)](https://www.morphllm.com/ollama-embedding-models) · [Best Embedding Models for RAG 2026](https://innovativeais.com/blog/best-embedding-models-for-rag-in-2026) · [Open Source Embedding Models 2026](https://pristren.com/blog/open-source-embedding-models/)
- [Chroma vs LanceDB](https://zilliz.com/comparison/chroma-vs-lancedb) · [Best Vector Databases 2026](https://encore.dev/articles/best-vector-databases)

Sekundárne zdroje (blogy, vendor obsah) — konkrétne čísla z nich (NDCG, recall, MTEB skóre) berte ako orientačné, nie ako reprodukované merania.

---

**Tento dokument zostáva analysis/audit, nie architektonický decision record.** Web tvrdenia a čísla sú orientačné, nie naše merania; žiadna ďalšia web rešerš ani implementácia nie je súčasťou tohto auditu. Ďalší krok zostáva Slice 1 podľa Proposal 006, s korekciami R4 a R6 zapracovanými do jeho návrhu pred začatím.
