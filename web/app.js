// URLs here are deliberately relative: the app is served at / locally and
// behind a /recut/ path prefix on the box, and relative paths work in both
// without the server being told which.
const $ = (id) => document.getElementById(id);
const DEFAULT_TARGETS = ["linkedin", "thread"];
let sourceRaw = "";
let lastJobId = null;

// ---------------------------------------------------------------- helpers

function esc(text) {
  const d = document.createElement("div");
  d.textContent = text;
  return d.innerHTML;
}

function setStatus(message, bad) {
  const el = $("status");
  el.textContent = message;
  el.className = bad ? "bad" : "";
}

// ---------------------------------------------------------------- targets

async function loadTargets() {
  const { targets } = await (await fetch("api/targets")).json();
  $("targets").innerHTML = targets
    .map(
      (t) =>
        `<label><input type="checkbox" value="${esc(t)}"${
          DEFAULT_TARGETS.includes(t) ? " checked" : ""
        }> ${esc(t)}</label>`
    )
    .join("");
}

const chosen = () =>
  [...document.querySelectorAll("#targets input:checked")].map((i) => i.value);

// ---------------------------------------------------------------- keys

// sessionStorage, not localStorage: a key that outlives the tab is a key the
// user has forgotten they left on a shared machine. Wrapped because a browser
// with site data blocked throws on access rather than returning nothing.
const KEYS = ["gemini", "groq"];

function readStored(name) {
  try {
    return sessionStorage.getItem(`recut.${name}`) || "";
  } catch {
    return "";
  }
}

function storeKey(name, value) {
  try {
    if (value) sessionStorage.setItem(`recut.${name}`, value);
    else sessionStorage.removeItem(`recut.${name}`);
  } catch {
    /* a tab that will not store is still a tab that can run a job */
  }
}

function restoreKeys() {
  KEYS.forEach((name) => {
    const el = $(name);
    el.value = readStored(name);
    el.addEventListener("change", () => storeKey(name, el.value.trim()));
  });
  if (KEYS.some((n) => $(n).value)) document.querySelector("details.keys").open = true;
}

$("forget").addEventListener("click", () => {
  KEYS.forEach((name) => {
    $(name).value = "";
    storeKey(name, "");
  });
  setStatus("keys cleared from this tab");
});

// ---------------------------------------------------------------- rendering

// The body is rebuilt from sentence spans rather than marked up in place, because
// the offsets come from the server and re-deriving them in the browser is how
// highlighting drifts by a character and points at the wrong line.
function renderBody(artifact) {
  let html = "";
  let cursor = 0;
  artifact.sentences.forEach((s, i) => {
    html += esc(artifact.body.slice(cursor, s.start));
    const anchored = s.claim_id
      ? ` data-claim="${esc(s.claim_id)}" data-i="${i}" title="${esc(s.claim)}"`
      : ' title="no cited claim matched this sentence"';
    html += `<s-line${anchored}>${esc(s.text)}</s-line>`;
    cursor = s.end;
  });
  return html + esc(artifact.body.slice(cursor));
}

// A target can emit files rather than prose. vidsmith emits a whole project
// directory, and until now the page showed the narration and quietly dropped
// the two files you actually need to build it.
function fileLines(artifact) {
  const files = Object.entries(artifact.files || {});
  if (!files.length) return "";
  const rows = files
    .map(([path, content]) => {
      const bytes = new Blob([content]).size;
      return `<button class="dl" data-target="${esc(artifact.target)}" data-path="${esc(path)}">
        ${esc(path)} <span class="sz">${bytes.toLocaleString()} bytes</span>
      </button>`;
    })
    .join("");
  return `<div class="files">
    <div class="fhint">Save these into one folder, keeping their names, then build it:
      <code>python -m vidsmith build &lt;that folder&gt;</code></div>
    ${rows}
  </div>`;
}

function wireDownloads(result) {
  const byTarget = Object.fromEntries(result.artifacts.map((a) => [a.target, a.files || {}]));
  document.querySelectorAll("button.dl").forEach((el) => {
    el.addEventListener("click", () => {
      const content = byTarget[el.dataset.target][el.dataset.path];
      // Basename on purpose: vidsmith expects script.md and config.yaml by those
      // exact names, so a flattened "vidsmith-script.md" would not build.
      const name = el.dataset.path.split("/").pop();
      const url = URL.createObjectURL(new Blob([content], { type: "text/plain" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  });
}

function warnLines(artifact) {
  return artifact.warnings
    .map(
      (w) =>
        `<div class="warn${w.severity === "notice" ? " notice" : ""}">${
          w.severity === "notice" ? "?" : "x"
        } <code>${esc(w.span)}</code> — ${esc(w.detail)}</div>`
    )
    .join("");
}

function render(result) {
  sourceRaw = result.source.raw || "";
  $("sourcebody").textContent = sourceRaw;

  const best = result.artifacts.reduce(
    (acc, a) => Math.max(acc, a.coverage || 0),
    0
  );
  $("cov").textContent = `${Math.round(best * 100)}% of lines traceable`;

  $("outputs").innerHTML = result.artifacts
    .map((a) => {
      const violation = a.meta && a.meta.format_violation;
      return `<div class="card" data-target="${esc(a.target)}">
        <h2>${esc(a.target)}
          <span class="tag ${a.clean ? "ok" : "bad"}">${
        a.clean ? "verified" : "unsupported spans"
      }</span>
        </h2>
        ${warnLines(a)}
        ${violation ? `<div class="warn notice">${esc(violation)}</div>` : ""}
        ${fileLines(a)}
        <div class="body">${renderBody(a)}</div>
      </div>`;
    })
    .join("");

  wireHover(result);
  wireDownloads(result);
}

function wireHover(result) {
  const byTarget = Object.fromEntries(
    result.artifacts.map((a) => [a.target, a.sentences])
  );

  document.querySelectorAll("s-line[data-claim]").forEach((el) => {
    const target = el.closest(".card").dataset.target;
    const sentence = byTarget[target][Number(el.dataset.i)];

    const show = () => {
      document
        .querySelectorAll("s-line.on")
        .forEach((n) => n.classList.remove("on"));
      el.classList.add("on");
      highlight(sentence.segments);
    };
    el.addEventListener("mouseenter", show);
    el.addEventListener("click", show);
  });
}

// Highlight by character offset, which is the invariant every adapter guarantees:
// raw[char_start:char_end] is exactly that segment's text, whether the source was
// a file, a web page or a transcript.
function highlight(segments) {
  if (!segments.length) return;
  const ranges = [...segments].sort((a, b) => a.char_start - b.char_start);
  let html = "";
  let cursor = 0;
  for (const r of ranges) {
    if (r.char_start < cursor) continue;
    html += esc(sourceRaw.slice(cursor, r.char_start));
    html += `<mark>${esc(sourceRaw.slice(r.char_start, r.char_end))}</mark>`;
    cursor = r.char_end;
  }
  html += esc(sourceRaw.slice(cursor));
  $("sourcebody").innerHTML = html;

  const first = $("sourcebody").querySelector("mark");
  if (first) first.scrollIntoView({ block: "center", behavior: "smooth" });
}

// ---------------------------------------------------------------- the run

async function poll(jobId) {
  for (;;) {
    const job = await (await fetch(`api/jobs/${jobId}`)).json();
    if (job.state === "done") return job.result;
    if (job.state === "failed") throw new Error(job.error.split("\n")[0]);
    setStatus(job.progress || "queued");
    await new Promise((r) => setTimeout(r, 1200));
  }
}

$("go").addEventListener("submit", async (event) => {
  event.preventDefault();
  const targets = chosen();
  if (!targets.length) return setStatus("pick at least one target", true);

  $("run").disabled = true;
  $("outputs").innerHTML = "";
  setStatus("starting");
  try {
    const response = await fetch("api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source: $("src").value.trim(),
        targets,
        gemini_key: $("gemini").value.trim() || null,
        groq_key: $("groq").value.trim() || null,
      }),
    });
    if (!response.ok) throw new Error((await response.json()).detail);
    const jobId = (await response.json()).id;
    const result = await poll(jobId);
    render(result);
    // Say whose quota paid for this. A page that quietly bills the host while
    // the user believes otherwise is the same class of dishonesty this whole
    // project is about.
    $("whose").textContent = result.byo_key
      ? "Ran on your key."
      : "Ran on this server's key.";
    $("whose").className = "whose" + (result.byo_key ? " mine" : "");
    lastJobId = jobId;
    $("queueit").hidden = false;
    const skipped = Object.entries(result.skipped || {});
    setStatus(
      `${result.claims.count} claims anchored, ${result.model_calls} model calls` +
        (skipped.length
          ? `. skipped ${skipped.map(([t, w]) => `${t} (${w})`).join(", ")}`
          : "")
    );
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    $("run").disabled = false;
  }
});

loadTargets();
restoreKeys();

// ---------------------------------------------------------------- the queue

const STATE_ACTIONS = {
  pending: [["approved", "Approve", true], ["rejected", "Reject", false]],
  approved: [["posted", "Mark posted", true], ["rejected", "Reject", false]],
  rejected: [["approved", "Approve after all", false]],
  posted: [],
};

let queueFilter = "pending";

function draftCard(d) {
  const acts = (STATE_ACTIONS[d.state] || [])
    .map(([to, label, primary]) =>
      `<button data-id="${esc(d.id)}" data-to="${esc(to)}"${primary ? ' class="primary"' : ""}>${esc(label)}</button>`)
    .join("");
  const note = d.note ? `<div class="fhint">note: ${esc(d.note)}</div>` : "";
  return `<div class="card draft">
    <h2>${esc(d.target)}
      <span><span class="state ${esc(d.state)}">${esc(d.state)}</span>
      <span class="when"> ${esc(d.source_title || d.source_ref || "")}</span></span>
    </h2>
    ${note}
    <div class="acts">
      ${acts}
      ${d.state === "pending" || d.state === "approved"
        ? `<input placeholder="why (optional, saved with the decision)" data-note="${esc(d.id)}">`
        : ""}
      <button data-open="${esc(d.id)}">Show provenance</button>
    </div>
    <div class="body">${esc(d.body)}</div>
    <div class="prov" id="prov-${esc(d.id)}"></div>
  </div>`;
}

async function loadQueue() {
  const data = await (await fetch("api/queue")).json();
  const counts = data.counts || {};
  $("qcount").textContent = counts.pending || 0;

  $("qfilters").innerHTML = ["pending", "approved", "rejected", "posted"]
    .map((s) => `<label><input type="radio" name="qf" value="${s}"${
      s === queueFilter ? " checked" : ""
    }> ${s} (${counts[s] || 0})</label>`)
    .join("");
  $("qfilters").querySelectorAll("input").forEach((el) =>
    el.addEventListener("change", () => { queueFilter = el.value; loadQueue(); })
  );

  const shown = (data.drafts || []).filter((d) => d.state === queueFilter);
  $("queue").innerHTML = shown.length
    ? shown.map(draftCard).join("")
    : `<div class="empty">Nothing ${esc(queueFilter)}.</div>`;
  wireQueue();
}

function wireQueue() {
  document.querySelectorAll("#queue button[data-to]").forEach((el) =>
    el.addEventListener("click", async () => {
      const id = el.dataset.id;
      const noteEl = document.querySelector(`input[data-note="${id}"]`);
      const response = await fetch(`api/queue/${id}/state`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state: el.dataset.to, note: (noteEl && noteEl.value.trim()) || null }),
      });
      if (!response.ok) return setStatus((await response.json()).detail, true);
      loadQueue();
    })
  );

  // Provenance is fetched per draft rather than with the list, because the
  // sentence map and the stored source are by far the largest columns.
  document.querySelectorAll("#queue button[data-open]").forEach((el) =>
    el.addEventListener("click", async () => {
      const id = el.dataset.open;
      const box = $(`prov-${id}`);
      if (box.innerHTML) return void (box.innerHTML = "");
      const d = await (await fetch(`api/queue/${id}`)).json();
      const rows = d.sentences.map((s) =>
        s.claim_id
          ? `<div class="fhint"><b>${esc(s.text.slice(0, 90))}</b><br>&nbsp;&nbsp;from: ${
              esc((s.segments[0] || {}).text || "")
            }</div>`
          : `<div class="fhint">${esc(s.text.slice(0, 90))}<br>&nbsp;&nbsp;<i>no cited claim matched this sentence</i></div>`
      ).join("");
      box.innerHTML = `<div class="files">${rows}</div>`;
    })
  );
}

$("sendqueue").addEventListener("click", async () => {
  if (!lastJobId) return;
  const response = await fetch(`api/jobs/${lastJobId}/queue`, { method: "POST" });
  if (!response.ok) return setStatus((await response.json()).detail, true);
  const { queued } = await response.json();
  $("queueit").hidden = true;
  setStatus(`${queued.length} draft(s) sent to the review queue`);
  loadQueue();
});

$("tab-make").addEventListener("click", () => showTab("make"));
$("tab-queue").addEventListener("click", () => showTab("queue"));

function showTab(name) {
  $("view-make").hidden = name !== "make";
  $("view-queue").hidden = name !== "queue";
  $("tab-make").className = name === "make" ? "on" : "";
  $("tab-queue").className = name === "queue" ? "on" : "";
  if (name === "queue") loadQueue();
}

loadQueue();
