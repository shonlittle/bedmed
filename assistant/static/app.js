"use strict";

// Text from the server is always placed with textContent. The one exception is rendered
// Markdown from the project's own documentation files.

const BED_STATUSES = {
  AVAILABLE: "Available",
  OCCUPIED: "Occupied",
  CLEANING: "Cleaning",
  RESERVED: "Reserved",
  OUT_OF_SERVICE: "Out of service",
};

const NODE_KINDS = {
  llm: "LLM: interprets the message",
  tool: "Tool: calls the hospital API",
  logic: "Logic: ordinary code, no external call",
};

const SUGGESTIONS = [
  "I need a bed for patient 1001",
  "Find a medical bed for Alvarez, no special needs",
  "Tele bed for P-1002, on a monitor, no isolation, can share",
  "ICU bed for P-1003, on a monitor, no isolation, can share",
  "What beds are free?",
  "Who is still waiting for a bed?",
];

const BOARD_REFRESH_MS = 4000;
const LOG_REFRESH_MS = 3000;

let sessionId = newSessionId();
let proposedBedId = null;
let refreshTimer = null;

const byId = (id) => document.getElementById(id);

function newSessionId() {
  return crypto.randomUUID();
}

function element(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined) node.textContent = options.text;
  for (const [name, value] of Object.entries(options.attributes || {})) node.setAttribute(name, value);
  for (const child of children) node.append(child);
  return node;
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    method: options.method || "GET",
    headers: options.body ? { "Content-Type": "application/json" } : {},
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload && typeof payload.detail === "string" ? payload.detail : `Request failed (${response.status}).`;
    throw new Error(detail);
  }
  return payload;
}

// --- Chat ---

function addMessage(role, text) {
  const bubble = element("div", { className: `message ${role}`, text });
  byId("messages").append(bubble);
  scrollMessagesToEnd();
  return bubble;
}

function scrollMessagesToEnd() {
  const messages = byId("messages");
  messages.scrollTop = messages.scrollHeight;
}

function traceFor(steps) {
  const rows = steps.map((step) =>
    element("li", {}, [
      element("span", { className: `badge ${step.kind}`, text: step.kind }),
      " ",
      element("strong", { text: step.node }),
      " ",
      element("code", { text: step.event }),
    ]),
  );
  const path = steps.map((step) => step.node).join(" → ");
  return element("details", { className: "trace" }, [
    element("summary", { text: `Trace: ${path}` }),
    element("ol", {}, rows),
  ]);
}

async function sendMessage(text) {
  const input = byId("chat-input");
  const sendButton = byId("chat-form").querySelector("button");
  addMessage("user", text);
  input.value = "";
  sendButton.disabled = true;
  try {
    const turn = await request("/api/chat", { method: "POST", body: { session_id: sessionId, message: text } });
    addMessage("assistant", turn.reply).append(traceFor(turn.steps));
    scrollMessagesToEnd();
    proposedBedId = turn.proposed_bed_id;
  } catch (error) {
    addMessage("problem", error.message);
  } finally {
    sendButton.disabled = false;
    input.focus();
    refreshBoard();
  }
}

function startConversation() {
  sessionId = newSessionId();
  proposedBedId = null;
  byId("messages").replaceChildren();
  addMessage("assistant", "Describe the patient and the bed they need, or pick an example below.");
  refreshBoard();
}

function renderSuggestions() {
  const buttons = SUGGESTIONS.map((text) => {
    const button = element("button", { text, attributes: { type: "button" } });
    button.addEventListener("click", () => sendMessage(text));
    return button;
  });
  byId("suggestions").replaceChildren(...buttons);
}

// --- Hospital board ---

function groupBy(items, keyOf) {
  const groups = new Map();
  for (const item of items) {
    const key = keyOf(item);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  return groups;
}

function statusMenu(bed) {
  const menu = element("select", { attributes: { "aria-label": `Change status of bed ${bed.id}` } });
  for (const [value, label] of Object.entries(BED_STATUSES)) {
    const option = element("option", { text: label, attributes: { value } });
    option.selected = value === bed.status;
    menu.append(option);
  }
  menu.disabled = Boolean(bed.patient_id);
  menu.addEventListener("change", () => changeBedStatus(bed.id, menu.value));
  return menu;
}

function bedTile(bed) {
  const features = [
    bed.isolation_capable ? "isolation" : null,
    bed.monitoring_capable ? "monitoring" : null,
  ].filter(Boolean);
  const occupant = bed.patient_id ? `${bed.patient_name} (${bed.patient_id})` : BED_STATUSES[bed.status];
  const proposed = bed.id === proposedBedId;
  return element("div", { className: `bed ${bed.status}${proposed ? " proposed" : ""}` }, [
    element("div", { className: "bed-id", text: proposed ? `${bed.id} · proposed` : bed.id }),
    element("div", { className: "bed-features", text: features.join(" · ") }),
    element("div", { className: "bed-occupant", text: occupant }),
    statusMenu(bed),
  ]);
}

function wardSection(beds) {
  const first = beds[0];
  const title = element("div", { className: "ward-title" }, [
    element("h3", { text: first.ward_name }),
    element("span", { className: "hint", text: first.ward_type.toLowerCase() }),
  ]);
  if (first.ward_status !== "OPEN") {
    title.append(element("span", { className: "closed", text: "Ward closed" }));
  }
  const rooms = [...groupBy(beds, (bed) => bed.room_id)].map(([roomId, roomBeds]) =>
    element("div", { className: "room" }, [
      element("div", { className: "room-title", text: `Room ${roomId} · ${roomBeds[0].room_type.toLowerCase()}` }),
      element("div", { className: "room-beds" }, roomBeds.map(bedTile)),
    ]),
  );
  return element("div", { className: "ward" }, [title, element("div", { className: "rooms" }, rooms)]);
}

function listItems(target, items, emptyText) {
  const rows = items.length ? items : [emptyText];
  byId(target).replaceChildren(...rows.map((text) => element("li", { text })));
}

function renderBoard(board) {
  const wards = [...groupBy(board.beds, (bed) => bed.ward_id).values()].map(wardSection);
  byId("wards").replaceChildren(...wards);

  const waiting = board.patients.filter((patient) => !patient.current_bed_id);
  listItems(
    "waiting",
    waiting.map((patient) => `${patient.full_name} (${patient.id})`),
    "Nobody is waiting.",
  );
  listItems(
    "escalations",
    board.escalations.map((item) => `#${item.id} · ${item.patient_id} · ${item.status} · ${item.reason}`),
    "No escalations.",
  );
}

async function refreshBoard() {
  if (byId("view-demo").hidden) return;
  // Leave the board alone while someone has a status menu open.
  if (document.activeElement && document.activeElement.tagName === "SELECT") return;
  const error = byId("board-error");
  try {
    renderBoard(await request("/api/board"));
    error.hidden = true;
  } catch (problem) {
    error.textContent = problem.message;
    error.hidden = false;
  }
}

async function changeBedStatus(bedId, status) {
  const error = byId("board-error");
  document.activeElement.blur();
  try {
    await request(`/api/demo/beds/${encodeURIComponent(bedId)}/status`, { method: "POST", body: { status } });
    error.hidden = true;
  } catch (problem) {
    error.textContent = problem.message;
    error.hidden = false;
  }
  refreshBoard();
}

function renderLegend() {
  const items = Object.entries(BED_STATUSES).map(([status, label]) =>
    element("li", {}, [element("span", { className: `swatch ${status}` }), label]),
  );
  byId("legend").replaceChildren(...items);
}

async function resetDemo() {
  try {
    await request("/api/demo/reset", { method: "POST" });
    startConversation();
  } catch (problem) {
    addMessage("problem", `Reset failed: ${problem.message}`);
  }
}

// --- Workflow diagram, documents, logs ---

async function renderDiagram(container, source) {
  const fallback = element("pre", { text: source });
  if (!window.mermaid) {
    container.replaceChildren(
      element("p", { className: "hint", text: "The diagram library could not be loaded, so the source is shown." }),
      fallback,
    );
    return;
  }
  try {
    const { svg } = await window.mermaid.render(`diagram-${crypto.randomUUID()}`, source);
    container.innerHTML = svg;
  } catch {
    container.replaceChildren(fallback);
  }
}

async function showWorkflow() {
  const graph = byId("graph");
  try {
    const workflow = await request("/api/workflow");
    const kinds = Object.entries(NODE_KINDS).map(([kind, label]) => {
      const names = workflow.nodes.filter((node) => node.kind === kind).map((node) => node.node);
      return element("li", {}, [
        element("span", { className: `badge ${kind}`, text: kind }),
        ` ${label} (${names.join(", ")})`,
      ]);
    });
    byId("node-kinds").replaceChildren(...kinds);
    await renderDiagram(graph, workflow.mermaid);
  } catch (problem) {
    graph.replaceChildren(element("p", { className: "error", text: problem.message }));
  }
}

async function showDocument(slug) {
  const article = byId("document");
  try {
    const page = await request(`/api/documents/${encodeURIComponent(slug)}`);
    byId("document-source").textContent = `Rendered from ${page.source}`;
    article.innerHTML = page.html;
    // Links to other files in the repository have nowhere to go in this UI, so show them as plain text.
    for (const link of article.querySelectorAll("a[href]")) {
      if (!/^(https?:|#)/.test(link.getAttribute("href"))) link.replaceWith(link.textContent);
    }
    for (const block of article.querySelectorAll("code.language-mermaid")) {
      const container = element("div", { className: "graph" });
      block.parentElement.replaceWith(container);
      await renderDiagram(container, block.textContent);
    }
  } catch (problem) {
    byId("document-source").textContent = "";
    article.replaceChildren(element("p", { className: "error", text: problem.message }));
  }
}

async function showLogs() {
  if (byId("view-logs").hidden) return;
  const body = byId("log-rows");
  try {
    const entries = await request("/api/logs");
    const rows = entries.map((entry) =>
      element("tr", { className: entry.level }, [
        element("td", { text: entry.time.replace("T", " ").replace("+00:00", "") }),
        element("td", { text: entry.level }),
        element("td", {}, [element("code", { text: entry.message })]),
      ]),
    );
    const empty = element("tr", {}, [
      element("td", { text: "No events yet. Send a message on the Demo page.", attributes: { colspan: "3" } }),
    ]);
    body.replaceChildren(...(rows.length ? rows : [empty]));
  } catch (problem) {
    body.replaceChildren(
      element("tr", {}, [element("td", { text: problem.message, attributes: { colspan: "3" } })]),
    );
  }
}

async function loadDocumentLinks() {
  try {
    const documents = await request("/api/documents");
    const links = documents.map((item) =>
      element("a", { text: item.title, attributes: { href: `#/document/${item.slug}` } }),
    );
    byId("document-links").replaceChildren(...links);
  } catch {
    byId("document-links").replaceChildren();
  }
  markActiveLink();
}

// --- Navigation ---

function markActiveLink() {
  const current = location.hash || "#/demo";
  for (const link of byId("nav").querySelectorAll("a")) {
    link.classList.toggle("active", link.getAttribute("href") === current);
  }
}

function route() {
  const [, view = "demo", argument] = (location.hash || "#/demo").split("/");
  const views = { demo: "view-demo", workflow: "view-workflow", document: "view-document", logs: "view-logs" };
  const active = views[view] || views.demo;
  for (const id of Object.values(views)) byId(id).hidden = id !== active;
  markActiveLink();

  clearInterval(refreshTimer);
  if (active === "view-demo") {
    refreshBoard();
    refreshTimer = setInterval(refreshBoard, BOARD_REFRESH_MS);
  } else if (active === "view-workflow") {
    showWorkflow();
  } else if (active === "view-document") {
    showDocument(argument || "");
  } else {
    showLogs();
    refreshTimer = setInterval(showLogs, LOG_REFRESH_MS);
  }
}

function start() {
  if (window.mermaid) window.mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });

  byId("chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const text = byId("chat-input").value.trim();
    if (text) sendMessage(text);
  });
  byId("new-conversation").addEventListener("click", startConversation);
  byId("reset-demo").addEventListener("click", resetDemo);
  window.addEventListener("hashchange", route);

  renderLegend();
  renderSuggestions();
  loadDocumentLinks();
  startConversation();
  route();
}

document.addEventListener("DOMContentLoaded", start);
