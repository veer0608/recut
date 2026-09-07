// URLs here are deliberately relative: the app is served at / locally and
// behind a /recut/ path prefix on the box, and relative paths work in both
// without the server being told which.
const $ = (id) => document.getElementById(id);
const DEFAULT_TARGETS = ["linkedin", "thread"];
let sourceRaw = "";

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
        <div class="body">${renderBody(a)}</div>
      </div>`;
    })
    .join("");

  wireHover(result);
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
    const result = await poll((await response.json()).id);
    render(result);
    // Say whose quota paid for this. A page that quietly bills the host while
    // the user believes otherwise is the same class of dishonesty this whole
    // project is about.
    $("whose").textContent = result.byo_key
      ? "Ran on your key."
      : "Ran on this server's key.";
    $("whose").className = "whose" + (result.byo_key ? " mine" : "");
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
