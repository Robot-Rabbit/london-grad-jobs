(() => {
const { liveSources, aiEnabled } = window.APP;
const $ = id => document.getElementById(id);
const PAGE = 30;
const STREAM_ERROR = "\x00";

// ---------- per-browser storage ----------
const store = {
  get(k, d) { try { const v = localStorage.getItem("lgj:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("lgj:" + k, JSON.stringify(v)); } catch {} },
};
// Shortlist keeps whole job objects: live listings come and go between searches.
let saved = new Map(store.get("shortlist", []).map(j => [j.id, j]));
const saveShortlist = () => store.set("shortlist", [...saved.values()]);

// ---------- dates ----------
const today = new Date(); today.setHours(0, 0, 0, 0);
const dayDiff = d => Math.round((new Date(d + "T00:00:00") - today) / 86400000);
const fmtDate = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });
const fmtTime = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" });

// ---------- state ----------
const state = { q: "", data: null, loading: false, sector: "All", source: "all", win: "any", sort: "deadline",
  salOnly: false, savedOnly: false, shown: PAGE, ai: null, target: null, error: "" };

const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ---------- fetching ----------
async function load(refresh = false) {
  state.loading = true; state.error = ""; render();
  try {
    const params = new URLSearchParams({ q: state.q });
    if (refresh) params.set("refresh", "1");
    const r = await fetch("/api/jobs?" + params);
    if (!r.ok) throw new Error();
    state.data = await r.json();
  } catch {
    state.error = "Couldn't load jobs. Check the server is running, then refresh.";
  }
  state.loading = false; state.shown = PAGE;
  renderChips(); render();
}

function allJobs() {
  const jobs = state.data?.jobs ?? [];
  if (!state.savedOnly) return jobs;
  return [...saved.values()];
}

function filtered() {
  let list = allJobs().filter(j => {
    if (state.sector !== "All" && j.sector !== state.sector) return false;
    if (state.source !== "all" && j.source !== state.source) return false;
    if (state.salOnly && !j.salary_min) return false;
    if (state.win === "14" && !(j.deadline && dayDiff(j.deadline) <= 14)) return false;
    if (state.win === "30" && !(j.deadline && dayDiff(j.deadline) <= 30)) return false;
    if (state.win === "new" && !(j.posted && dayDiff(j.posted) >= -7)) return false;
    return true;
  });
  if (state.ai) {
    const rank = new Map(state.ai.matches.map((m, i) => [m.id, i]));
    return list.filter(j => rank.has(j.id)).sort((a, b) => rank.get(a.id) - rank.get(b.id));
  }
  const dkey = j => j.deadline ? dayDiff(j.deadline) : 9999;
  const pkey = j => j.posted ? dayDiff(j.posted) : -9999;
  if (state.sort === "deadline") list.sort((a, b) => dkey(a) - dkey(b) || pkey(b) - pkey(a));
  if (state.sort === "newest") list.sort((a, b) => pkey(b) - pkey(a));
  if (state.sort === "salary") list.sort((a, b) => (b.salary_annual || 0) - (a.salary_annual || 0));
  return list;
}

// ---------- rendering ----------
function renderChips() {
  const jobs = allJobs();
  const counts = {};
  jobs.forEach(j => counts[j.sector] = (counts[j.sector] || 0) + 1);
  const sectors = Object.keys(counts).sort((a, b) => counts[b] - counts[a]);
  if (state.sector !== "All" && !counts[state.sector]) state.sector = "All";
  $("chips").innerHTML = [["All", jobs.length], ...sectors.map(s => [s, counts[s]])].map(([s, n]) =>
    `<button type="button" class="chip" data-s="${esc(s)}" aria-pressed="${state.sector === s}">${esc(s)}<span class="n">${n}</span></button>`
  ).join("");
}

function dueChip(j) {
  if (j.deadline) {
    const dl = dayDiff(j.deadline);
    const cls = dl <= 7 ? "crit" : dl <= 21 ? "warn" : "ok";
    const label = dl <= 0 ? "Closes today" : dl === 1 ? "1 day left" : `${dl} days left`;
    return `<div class="due ${cls}">${label}<small>${fmtDate.format(new Date(j.deadline))}</small></div>`;
  }
  if (j.source === "employer") {
    // Careers-site postings often stay open for months; a stale "posted" date misleads.
    const ago = j.posted ? -dayDiff(j.posted) : 999;
    return `<div class="due ok">${ago <= 14 ? "New" : "Open now"}<small>on employer site</small></div>`;
  }
  if (j.posted) {
    const ago = -dayDiff(j.posted);
    return `<div class="due na">${ago <= 0 ? "Posted today" : ago === 1 ? "Posted yesterday" : `Posted ${ago}d ago`}<small>no closing date</small></div>`;
  }
  return `<div class="due na">Deadline<small>see listing</small></div>`;
}

const SRC_LABEL = { scheme: "Grad scheme", employer: "Employer careers", employers: "employer careers sites", reed: "Reed", adzuna: "Adzuna" };

function renderFeedline() {
  const d = state.data;
  const bits = [];
  if (liveSources.length) {
    bits.push(`<span class="live">Live from ${liveSources.map(s => SRC_LABEL[s]).join(" + ")}</span>`);
  } else {
    bits.push(`<span class="off">Live feed off: add REED_API_KEY or Adzuna keys to .env. Showing the scheme snapshot only.</span>`);
  }
  if (d?.fetched_at) bits.push(`<span>Updated ${fmtTime.format(new Date(d.fetched_at))}</span>`);
  (d?.errors || []).forEach(e => bits.push(`<span class="off">${esc(e)}</span>`));
  bits.push(`<button class="linkbtn" id="refresh" type="button">Refresh</button>`);
  $("feedline").innerHTML = bits.join("");
  $("refresh").onclick = () => load(true);
  const total = d?.jobs?.length ?? 0;
  $("stamp").innerHTML = state.loading ? "Loading…" : `<b>${total}</b> London roles${state.q ? ` for “${esc(state.q)}”` : ""}`;
}

function render() {
  renderFeedline();
  if (state.loading) {
    $("jobs").innerHTML = '<div class="skeleton"></div>'.repeat(5);
    $("moreBtn").hidden = true; $("count").textContent = "";
    return;
  }
  if (state.error) { $("jobs").innerHTML = `<div class="empty">${esc(state.error)}</div>`; return; }
  const list = filtered();
  $("count").textContent = `${list.length} ${list.length === 1 ? "role" : "roles"}`;
  $("listTitle").textContent = state.ai ? "Smart match results" : state.savedOnly ? "Your shortlist" : "Graduate roles in London";
  const why = state.ai ? Object.fromEntries(state.ai.matches.map(m => [m.id, m.why])) : {};
  const page = list.slice(0, state.shown);
  $("jobs").innerHTML = page.length ? page.map(j => `
    <article class="job${state.target === j.id ? " selected" : ""}" data-id="${esc(j.id)}">
      <div style="min-width:0">
        <div class="emp">${esc(j.employer)}</div>
        <h3>${esc(j.title)}</h3>
        <div class="meta">
          <span class="src ${j.source}">${SRC_LABEL[j.source] || esc(j.source)}</span>
          <span>${esc(j.sector)}</span>
          <span>${esc(j.area)}</span>
          <span>${j.salary_label ? `<b>${esc(j.salary_label)}</b>` : "Salary not listed"}</span>
        </div>
      </div>
      ${dueChip(j)}
      ${j.source !== "scheme" && j.description ? `<p class="desc">${esc(j.description)}</p>` : ""}
      ${why[j.id] ? `<div class="why"><b>Why it fits:</b> ${esc(why[j.id])}</div>` : ""}
      <div class="actions">
        <button class="btn small primary" data-act="tailor">Tailor application<span class="ai-tag">AI</span></button>
        <button class="btn small" data-act="save" aria-pressed="${saved.has(j.id)}">${saved.has(j.id) ? "★ Shortlisted" : "☆ Shortlist"}</button>
        <a class="btn small" href="${esc(j.url)}" target="_blank" rel="noopener">View &amp; apply ↗</a>
      </div>
    </article>`).join("")
    : `<div class="empty">${state.savedOnly ? "Your shortlist is empty. Use ☆ Shortlist on any role to keep it here." : "No roles match. Try a broader search or clear some filters."}</div>`;
  $("moreBtn").hidden = list.length <= state.shown;
  $("moreBtn").textContent = `Show more (${list.length - state.shown} left)`;
}

// ---------- events ----------
$("chips").addEventListener("click", e => {
  const b = e.target.closest(".chip"); if (!b) return;
  state.sector = b.dataset.s; state.shown = PAGE; renderChips(); render();
});
$("jobs").addEventListener("click", e => {
  const b = e.target.closest("button[data-act]"); if (!b) return;
  const id = b.closest(".job").dataset.id;
  const job = allJobs().find(j => j.id === id);
  if (b.dataset.act === "save") {
    saved.has(id) ? saved.delete(id) : saved.set(id, job);
    saveShortlist(); if (state.savedOnly) renderChips(); render();
  } else setTarget(job);
});
$("moreBtn").onclick = () => { state.shown += PAGE; render(); };
$("qform").addEventListener("submit", e => {
  e.preventDefault(); clearAi();
  state.q = $("q").value.trim(); state.savedOnly = false; $("savedOnly").checked = false;
  load();
});
const bind = (id, key, ev = "change", prop = "value") => $(id).addEventListener(ev, e => {
  state[key] = e.target[prop]; state.shown = PAGE; if (key === "sort") clearAi(); if (key === "savedOnly") renderChips(); render();
});
bind("source", "source"); bind("window", "win"); bind("sort", "sort");
bind("salOnly", "salOnly", "change", "checked"); bind("savedOnly", "savedOnly", "change", "checked");

// ---------- profile ----------
const EXAMPLE_PROFILE = `BSc Economics, University of Leeds (expected 2:1, graduating July 2027)
Modules: Econometrics, Financial Economics, Data Analysis in R

Summer Analyst Intern, mid-size accountancy firm, London (Jun–Aug 2026)
- Reconciled client ledgers for 12 SME audits; flagged 3 material discrepancies
- Built an Excel model that cut monthly reporting prep from 2 days to half a day

Treasurer, University Investment Society (2025–26)
- Managed £8k budget; grew membership from 90 to 210

Part-time barista, 15 hrs/week throughout university
Skills: Excel (pivot tables, VLOOKUP), R, SQL basics, PowerPoint`;
const prof = $("profile");
prof.value = store.get("profile", null) ?? EXAMPLE_PROFILE;
const syncEg = () => { $("egTag").hidden = prof.value.trim() !== EXAMPLE_PROFILE.trim(); };
syncEg();
let profTimer;
prof.addEventListener("input", () => {
  syncEg(); clearTimeout(profTimer);
  profTimer = setTimeout(() => { store.set("profile", prof.value); $("profSaved").textContent = "Saved in this browser"; }, 400);
});
$("clearProf").onclick = () => { prof.value = ""; store.set("profile", ""); syncEg(); prof.focus(); };

// ---------- AI ----------
if (!aiEnabled) {
  $("aiUnavailable").hidden = false;
  ["aiSearch", "genHi", "genCl"].forEach(id => $(id).disabled = true);
}
const thinking = msg => `<div class="status"><span class="dot"></span>${msg}</div>`;

async function postJSON(url, payload) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || "Something went wrong. Try again.");
  return data;
}

function clearAi() { state.ai = null; $("aiBanner").hidden = true; }

$("aiSearch").onclick = async () => {
  const want = $("q").value.trim();
  if (!want && !prof.value.trim()) { $("q").focus(); return; }
  const btn = $("aiSearch"); btn.disabled = true;
  const banner = $("aiBanner"); banner.hidden = false;
  banner.innerHTML = thinking("Matching roles to your request and profile…");
  try {
    // Rank across the broad graduate feed, not just the current keyword results.
    const { matches } = await postJSON("/api/ai/match", { request: want, profile: prof.value, q: "" });
    if (state.q) { state.q = ""; await load(); }
    if (!matches.length) { banner.textContent = "No strong matches. Try describing the kind of work you want."; return; }
    state.ai = { matches }; state.savedOnly = false; $("savedOnly").checked = false;
    banner.innerHTML = `<span>Ranked by Claude for: <b>${esc(want || "your profile")}</b></span><button class="btn small" id="clearAi" type="button">Show all roles</button>`;
    $("clearAi").onclick = () => { clearAi(); render(); };
    render();
  } catch (e) {
    banner.innerHTML = `<span class="err">${esc(e.message)}</span>`;
  } finally { btn.disabled = !aiEnabled; }
};

function setTarget(job) {
  state.target = job.id; state.targetJob = job;
  $("noTarget").hidden = true; $("targetBox").hidden = false;
  $("tEmp").textContent = job.employer; $("tTitle").textContent = job.title;
  $("outHi").innerHTML = ""; $("outCl").innerHTML = "";
  $("copyHi").hidden = $("copyCl").hidden = true;
  $("jd").value = store.get("jd:" + job.id, "");
  render();
  if (matchMedia("(max-width: 960px)").matches) $("aiPanel").scrollIntoView({ behavior: "smooth", block: "start" });
}
$("jd").addEventListener("input", () => { if (state.target) store.set("jd:" + state.target, $("jd").value); });

function tab(which) {
  const hi = which === "hi";
  $("tabHi").setAttribute("aria-selected", hi); $("tabCl").setAttribute("aria-selected", !hi);
  $("paneHi").hidden = !hi; $("paneCl").hidden = hi;
}
$("tabHi").onclick = () => tab("hi"); $("tabCl").onclick = () => tab("cl");

const aiPayload = () => ({ job_id: state.target, job: state.targetJob, job_description: $("jd").value, profile: prof.value });

let lastHi = "";
$("genHi").onclick = async () => {
  if (!state.target) return;
  const btn = $("genHi"); btn.disabled = true; $("copyHi").hidden = true;
  $("outHi").innerHTML = thinking("Reading your profile against the role…");
  try {
    const r = await postJSON("/api/ai/highlights", aiPayload());
    $("outHi").innerHTML =
      `<ul>${r.highlights.map(h => `<li>${esc(h.bullet)}<small>${esc(h.why)}</small></li>`).join("")}</ul>` +
      (r.keywords?.length ? `<div class="sub" style="margin:12px 0 0">Words to mirror in your CV</div><div class="kw">${r.keywords.map(k => `<span>${esc(k)}</span>`).join("")}</div>` : "") +
      (r.gaps?.length ? `<div class="gaps"><div class="sub" style="margin:0">Gaps to address</div><ul>${r.gaps.map(g => `<li>${esc(g)}</li>`).join("")}</ul></div>` : "");
    lastHi = r.highlights.map(h => "• " + h.bullet).join("\n");
    $("copyHi").hidden = false;
  } catch (e) {
    $("outHi").innerHTML = `<p class="err">${esc(e.message)}</p>`;
  } finally { btn.disabled = !aiEnabled; }
};

let clCtl = null, lastCl = "";
$("genCl").onclick = async () => {
  if (!state.target) return;
  const btn = $("genCl"); btn.disabled = true; $("stopCl").hidden = false; $("copyCl").hidden = true;
  $("outCl").innerHTML = thinking("Drafting your letter…");
  clCtl = new AbortController();
  let text = "";
  const show = () => { $("outCl").innerHTML = '<div class="letter"></div>'; $("outCl").firstChild.textContent = text; };
  try {
    const r = await fetch("/api/ai/cover-letter", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...aiPayload(), tone: $("tone").value }), signal: clCtl.signal,
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || "Something went wrong. Try again.");
    const reader = r.body.getReader(), dec = new TextDecoder();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      text += dec.decode(value, { stream: true });
      const errAt = text.indexOf(STREAM_ERROR);
      if (errAt >= 0) { const msg = text.slice(errAt + 1); text = text.slice(0, errAt); if (text) show(); throw new Error(msg); }
      show();
    }
    lastCl = text; $("copyCl").hidden = !text;
  } catch (e) {
    if (e.name === "AbortError") { lastCl = text; $("copyCl").hidden = !text; }
    else $("outCl").insertAdjacentHTML(text ? "beforeend" : "afterbegin", `<p class="err">${esc(e.message)}</p>`);
    if (!text && e.name !== "AbortError") $("outCl").querySelector(".status")?.remove();
  } finally { btn.disabled = !aiEnabled; $("stopCl").hidden = true; }
};
$("stopCl").onclick = () => clCtl?.abort();

async function copy(text, btn) {
  try { await navigator.clipboard.writeText(text); btn.textContent = "Copied"; }
  catch { btn.textContent = "Copy failed: select the text instead"; }
  setTimeout(() => btn.textContent = "Copy", 1800);
}
$("copyHi").onclick = e => copy(lastHi, e.currentTarget);
$("copyCl").onclick = e => copy(lastCl, e.currentTarget);

load();
})();
