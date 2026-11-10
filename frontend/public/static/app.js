// Scheme Saathi page: builds the form from /api/form, streams the investigation, asks questions, shows results.
"use strict";

const T = {
  en: {
    tagline: "Finds government schemes you may be missing, from official websites", language: "Language",
    about_you: "About you", privacy: "We never ask for Aadhaar, bank or phone numbers. Fill what you know; leave the rest blank.",
    search_web: "Search official websites (1-3 min)", find: "Find my schemes", saathi: "Saathi", results: "Your schemes",
    status_idle: "Waiting for your details", status_investigating: "Investigating…", status_asking: "Question for you",
    status_thinking: "Thinking…", status_done: "Done", no_results: "Results appear here as soon as you start.",
    ask_placeholder: "Ask about any scheme, e.g. which documents do I need?", ask: "Ask",
    start_with: "Start with this", lesser_known: "Lesser-known schemes you may be missing",
    lesser_note: "Run by districts, departments, welfare boards or corporations; few people apply for these.",
    also: "Also eligible", near: "Almost eligible", check_page: "May apply: check the official page",
    none_yet: "No scheme confirmed yet. Answer the questions to narrow it down.",
    why: "Why it fits", check_yourself: "Please check yourself", documents: "Documents", source: "Source",
    why_ranked: "Why it is ranked here", worth: "Worth about", per_year: "a year",
    see_rules: "See rules and quotes", not_verified: "Not yet checked by a person", lesser: "Lesser-known",
    eligible: "Eligible", likely: "Likely eligible", almost: "Almost", skip: "Skip", send: "Send",
    type_answer: "Or type your answer", yes: "Yes", no: "No", choose: "—", you: "You", reason: "Why I ask",
    checked: (a, b) => `${a} schemes checked · ${b} need more information`, thinking: "Reasoning",
    action_crawl: "Reading site", action_search: "Searching", found: "Found", notice: "Note",
    fields: {
      state: "State", district: "District", age: "Age", gender: "Gender", marital_status: "Marital status",
      social_category: "Category", occupation: "Main work", annual_family_income_inr: "Family income per year (₹)",
      ration_card_type: "Ration card", disability_percent: "Disability % (0 if none)", num_children: "Number of children",
      has_girl_child_under_10: "Daughter under 10?", is_student: "Currently studying?",
      owns_cultivable_land: "Family owns farm land?", is_minority: "Minority community?",
    },
  },
  hi: {
    tagline: "सरकारी वेबसाइटों से वे योजनाएँ ढूँढता है जो आपसे छूट रही हैं", language: "भाषा",
    about_you: "आपके बारे में", privacy: "हम आधार, बैंक या फ़ोन नंबर कभी नहीं पूछते। जो पता है वह भरें, बाकी खाली छोड़ें।",
    search_web: "सरकारी वेबसाइटें खोजें (1-3 मिनट)", find: "मेरी योजनाएँ खोजें", saathi: "साथी", results: "आपकी योजनाएँ",
    status_idle: "आपकी जानकारी का इंतज़ार", status_investigating: "खोज जारी…", status_asking: "आपसे एक सवाल",
    status_thinking: "सोच रहा हूँ…", status_done: "पूरा", no_results: "शुरू करते ही नतीजे यहाँ दिखेंगे।",
    ask_placeholder: "किसी भी योजना के बारे में पूछें, जैसे कौन से दस्तावेज़ चाहिए?", ask: "पूछें",
    start_with: "इससे शुरू करें", lesser_known: "कम जानी-पहचानी योजनाएँ जो आपसे छूट सकती हैं",
    lesser_note: "ज़िला, विभाग, कल्याण बोर्ड या निगम चलाते हैं; बहुत कम लोग आवेदन करते हैं।",
    also: "और योजनाएँ", near: "लगभग पात्र", check_page: "लागू हो सकती है: आधिकारिक पेज देखें",
    none_yet: "अभी कोई योजना पक्की नहीं। सवालों के जवाब दें।",
    why: "क्यों उपयुक्त", check_yourself: "खुद जाँचें", documents: "दस्तावेज़", source: "स्रोत",
    why_ranked: "यह पहले क्यों", worth: "अनुमानित मूल्य", per_year: "प्रति वर्ष",
    see_rules: "नियम और उद्धरण देखें", not_verified: "अभी किसी व्यक्ति ने जाँचा नहीं", lesser: "कम जानी-पहचानी",
    eligible: "पात्र", likely: "संभवतः पात्र", almost: "लगभग", skip: "छोड़ें", send: "भेजें",
    type_answer: "या अपना जवाब लिखें", yes: "हाँ", no: "नहीं", choose: "—", you: "आप", reason: "क्यों पूछ रहा हूँ",
    checked: (a, b) => `${a} योजनाएँ जाँचीं · ${b} के लिए और जानकारी चाहिए`, thinking: "विचार",
    action_crawl: "साइट पढ़ रहा हूँ", action_search: "खोज रहा हूँ", found: "मिली", notice: "सूचना",
    fields: {
      state: "राज्य", district: "ज़िला", age: "उम्र", gender: "लिंग", marital_status: "वैवाहिक स्थिति",
      social_category: "वर्ग", occupation: "मुख्य काम", annual_family_income_inr: "परिवार की सालाना आय (₹)",
      ration_card_type: "राशन कार्ड", disability_percent: "विकलांगता % (न हो तो 0)", num_children: "बच्चों की संख्या",
      has_girl_child_under_10: "10 साल से छोटी बेटी?", is_student: "अभी पढ़ाई कर रहे हैं?",
      owns_cultivable_land: "परिवार के पास खेती की ज़मीन?", is_minority: "अल्पसंख्यक समुदाय?",
    },
  },
  mr: {
    tagline: "सरकारी संकेतस्थळांवरून तुमच्याकडून सुटलेल्या योजना शोधतो", language: "भाषा",
    about_you: "तुमच्याबद्दल", privacy: "आम्ही आधार, बँक किंवा फोन नंबर कधीच विचारत नाही. माहीत आहे ते भरा, बाकी रिकामे ठेवा.",
    search_web: "सरकारी संकेतस्थळे शोधा (1-3 मिनिटे)", find: "माझ्या योजना शोधा", saathi: "साथी", results: "तुमच्या योजना",
    status_idle: "तुमच्या माहितीची प्रतीक्षा", status_investigating: "शोध सुरू…", status_asking: "तुमच्यासाठी प्रश्न",
    status_thinking: "विचार करत आहे…", status_done: "पूर्ण", no_results: "सुरू करताच निकाल इथे दिसतील.",
    ask_placeholder: "कोणत्याही योजनेबद्दल विचारा, उदा. कोणती कागदपत्रे लागतात?", ask: "विचारा",
    start_with: "यापासून सुरुवात करा", lesser_known: "कमी माहीत असलेल्या योजना ज्या सुटू शकतात",
    lesser_note: "जिल्हा, विभाग, कल्याण मंडळ किंवा महामंडळ चालवतात; फार कमी लोक अर्ज करतात.",
    also: "इतर योजना", near: "जवळजवळ पात्र", check_page: "लागू असू शकते: अधिकृत पान पहा",
    none_yet: "अजून कोणतीही योजना निश्चित नाही. प्रश्नांची उत्तरे द्या.",
    why: "का जुळते", check_yourself: "स्वतः तपासा", documents: "कागदपत्रे", source: "स्रोत",
    why_ranked: "हे प्रथम का", worth: "अंदाजे मूल्य", per_year: "प्रति वर्ष",
    see_rules: "नियम आणि उतारे पहा", not_verified: "अजून कोणी तपासलेले नाही", lesser: "कमी माहीत",
    eligible: "पात्र", likely: "बहुधा पात्र", almost: "जवळजवळ", skip: "वगळा", send: "पाठवा",
    type_answer: "किंवा तुमचे उत्तर लिहा", yes: "हो", no: "नाही", choose: "—", you: "तुम्ही", reason: "का विचारतो",
    checked: (a, b) => `${a} योजना तपासल्या · ${b} साठी अधिक माहिती हवी`, thinking: "विचार",
    action_crawl: "संकेतस्थळ वाचत आहे", action_search: "शोधत आहे", found: "सापडली", notice: "सूचना",
    fields: {
      state: "राज्य", district: "जिल्हा", age: "वय", gender: "लिंग", marital_status: "वैवाहिक स्थिती",
      social_category: "प्रवर्ग", occupation: "मुख्य काम", annual_family_income_inr: "कुटुंबाचे वार्षिक उत्पन्न (₹)",
      ration_card_type: "रेशन कार्ड", disability_percent: "अपंगत्व % (नसल्यास 0)", num_children: "मुलांची संख्या",
      has_girl_child_under_10: "10 वर्षांखालील मुलगी?", is_student: "सध्या शिकत आहात?",
      owns_cultivable_land: "कुटुंबाकडे शेतजमीन?", is_minority: "अल्पसंख्याक समुदाय?",
    },
  },
};

const $ = (sel) => document.querySelector(sel);
let lang = localStorageGet("lang") || "en";
let formSpec = null;
let session = null;
let source = null;

function localStorageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localStorageSet(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } }
function t(key) { return (T[lang] && T[lang][key]) || T.en[key] || key; }
function fieldLabel(name) { return (T[lang].fields[name]) || T.en.fields[name] || name; }
function human(v) {
  if (v === true) return t("yes");
  if (v === false) return t("no");
  const s = String(v).replace(/_/g, " ");
  return ["sc", "st", "obc", "ews", "bpl", "apl"].includes(String(v)) ? s.toUpperCase() : s.charAt(0).toUpperCase() + s.slice(1);
}
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) n.append(kid);
  return n;
}

// ---------- language ----------
function applyLang() {
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i18n]").forEach((n) => { n.textContent = t(n.dataset.i18n); });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((n) => { n.placeholder = t(n.dataset.i18nPlaceholder); });
  if (formSpec) buildForm(readForm());
}
$("#lang").value = lang;
$("#lang").addEventListener("change", (e) => { lang = e.target.value; localStorageSet("lang", lang); applyLang(); });

// ---------- form ----------
function buildForm(values = {}) {
  const form = $("#profile-form");
  form.innerHTML = "";
  for (const f of formSpec.fields) {
    let input;
    const id = `f-${f.name}`;
    if (f.name === "district" && (values.state || "maharashtra") === "maharashtra") {
      input = el("select", { id }, el("option", { value: "" }, t("choose")),
        formSpec.districts.maharashtra.map((d) => el("option", { value: d }, human(d))));
    } else if (f.type === "enum") {
      input = el("select", { id }, el("option", { value: "" }, t("choose")), f.values.map((v) => el("option", { value: v }, human(v))));
    } else if (f.type === "bool") {
      input = el("select", { id }, el("option", { value: "" }, t("choose")),
        el("option", { value: "true" }, t("yes")), el("option", { value: "false" }, t("no")));
    } else if (f.type === "int" || f.type === "number") {
      input = el("input", { id, type: "number", min: f.min ?? 0, max: f.max ?? null, step: f.type === "int" ? 1 : "any", inputmode: "numeric" });
    } else {
      input = el("input", { id, type: "text" });
    }
    if (values[f.name] !== undefined) input.value = String(values[f.name]);
    if (f.name === "state") {
      if (!values.state) input.value = "maharashtra";
      input.addEventListener("change", () => buildForm(readForm()));
    }
    form.append(el("div", { class: "field" }, el("label", { for: id }, fieldLabel(f.name)), input));
  }
}
function readForm() {
  const out = {};
  if (!formSpec) return out;
  for (const f of formSpec.fields) {
    const n = document.getElementById(`f-${f.name}`);
    if (!n || n.value === "") continue;
    if (f.type === "bool") out[f.name] = n.value === "true";
    else if (f.type === "int") out[f.name] = parseInt(n.value, 10);
    else if (f.type === "number") out[f.name] = parseFloat(n.value);
    else out[f.name] = n.value;
  }
  return out;
}

async function start() {
  const err = $("#form-error");
  err.hidden = true;
  const profile = readForm();
  $("#start").disabled = true;
  try {
    const r = await fetch("/api/sessions", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile, lang, web: $("#web").checked }) });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      const errors = body.detail && body.detail.errors ? body.detail.errors.join("; ") : `HTTP ${r.status}`;
      throw new Error(errors);
    }
    session = (await r.json()).id;
    $("#timeline").innerHTML = "";
    $("#results").innerHTML = "";
    $("#ask-form").hidden = false;
    setStatus("status_investigating", "busy");
    listen();
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
  } finally {
    $("#start").disabled = false;
  }
}
$("#start").addEventListener("click", start);

// ---------- events ----------
function listen() {
  if (source) source.close();
  source = new EventSource(`/api/sessions/${session}/events`);
  source.onmessage = (m) => handle(JSON.parse(m.data));
}
function setStatus(key, cls = "") { const s = $("#status"); s.textContent = t(key); s.className = `pill ${cls}`; }
function log(kind, label, body, extra) {
  const li = el("li", { class: kind }, el("span", { class: "kind" }, label), body, extra || null);
  $("#timeline").prepend(li);
}

function handle(ev) {
  const d = ev.data;
  switch (ev.kind) {
    case "thinking":
      log("thinking", t("thinking"), d.summary || "",
        (d.needs || d.uncovered || []).length ? el("ul", {}, (d.needs || d.uncovered).map((n) =>
          el("li", {}, typeof n === "string" ? n : `${n.need}: ${n.why}`))) : null);
      break;
    case "action":
      log("action", d.kind === "crawl" ? t("action_crawl") : t("action_search"), `${d.target}`, el("div", { class: "muted small" }, d.why));
      break;
    case "found":
      log("found", t("found"), `${d.name}`, el("div", { class: "muted small" },
        `${d.site}${d.awareness !== "high" ? " · " + t("lesser") : ""}`));
      break;
    case "notice": case "progress":
      log("notice", t("notice"), d.text);
      break;
    case "results":
      renderResults(d);
      break;
    case "question":
      renderQuestion(d);
      setStatus("status_asking", "busy");
      break;
    case "answer_recorded":
      $("#question").hidden = true;
      setStatus("status_thinking", "busy");
      break;
    case "reply":
      log("reply", t("saathi"), d.text);
      break;
    case "done":
      $("#question").hidden = true;
      setStatus("status_done", "ok");
      break;
  }
}

// ---------- questions ----------
function renderQuestion(q) {
  const box = $("#question");
  box.innerHTML = "";
  const send = (payload) => answer(payload);
  let controls = null;
  if (q.type === "bool") {
    controls = el("div", { class: "choices" },
      el("button", { type: "button", onclick: () => send({ field: q.field, value: true }) }, t("yes")),
      el("button", { type: "button", onclick: () => send({ field: q.field, value: false }) }, t("no")));
  } else if (q.type === "enum") {
    controls = el("div", { class: "choices" }, q.choices.map((c) =>
      el("button", { type: "button", onclick: () => send({ field: q.field, value: c }) }, human(c))));
  } else if (q.type === "int" || q.type === "number") {
    const inp = el("input", { type: "number", min: 0 });
    controls = el("div", { class: "answer-row" }, inp,
      el("button", { type: "button", class: "secondary", onclick: () => inp.value !== "" &&
        send({ field: q.field, value: q.type === "int" ? parseInt(inp.value, 10) : parseFloat(inp.value) }) }, t("send")));
  }
  const free = el("input", { type: "text", placeholder: t("type_answer") });
  box.append(
    el("div", { class: "q" }, q.question),
    el("div", { class: "why" }, `${t("reason")}: ${q.reason}`),
    controls,
    el("div", { class: "answer-row" }, free,
      el("button", { type: "button", class: "secondary", onclick: () => free.value.trim() && send({ text: free.value.trim() }) }, t("send")),
      el("button", { type: "button", onclick: () => send({ skip: true }) }, t("skip"))),
  );
  box.hidden = false;
}
async function answer(payload) {
  $("#question").querySelectorAll("button").forEach((b) => { b.disabled = true; });
  const r = await fetch(`/api/sessions/${session}/answer`, { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  if (!r.ok) {
    $("#question").querySelectorAll("button").forEach((b) => { b.disabled = false; });
    log("notice", t("notice"), (await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
    return;
  }
  const label = payload.skip ? t("skip") : payload.text || human(payload.value);
  log("you", t("you"), label);
}

$("#ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("#ask-text").value.trim();
  if (!text || !session) return;
  $("#ask-text").value = "";
  log("you", t("you"), text);
  await fetch(`/api/sessions/${session}/ask`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }) });
});

// ---------- results ----------
function inr(n) { return n ? `₹${Number(n).toLocaleString("en-IN")}/year` : ""; }
function card(c, featured = false) {
  const statusLabel = { eligible: t("eligible"), eligible_check_manually: t("likely"), near_miss: t("almost") }[c.status] || c.status;
  return el("article", { class: `card${featured ? " featured" : ""}` },
    el("h4", {}, c.name),
    el("div", { class: "badges" },
      el("span", { class: "badge ok" }, statusLabel),
      c.awareness !== "high" ? el("span", { class: "badge low" }, t("lesser")) : null,
      c.verified ? null : el("span", { class: "badge unverified" }, t("not_verified")),
      c.district ? el("span", { class: "badge" }, human(c.district)) : null),
    c.benefit ? el("div", { class: "benefit" }, c.benefit) : null,
    c.annual_value_inr ? el("div", { class: "worth" }, `${t("worth")} ₹${Number(c.annual_value_inr).toLocaleString("en-IN")} ${t("per_year")}`) : null,
    c.why_ranked && c.why_ranked.length ? el("details", { class: "why-ranked" },
      el("summary", {}, t("why_ranked")), el("ul", {}, c.why_ranked.map((r) => el("li", {}, r)))) : null,
    c.status === "near_miss" ? el("div", { class: "small" }, c.why) : null,
    c.rules_met && c.rules_met.length ? el("div", { class: "small" }, el("strong", {}, t("why") + ": "),
      el("ul", {}, c.rules_met.slice(0, 4).map((r) => el("li", {}, r)))) : null,
    c.check_yourself && c.check_yourself.length ? el("div", { class: "small" }, el("strong", {}, t("check_yourself") + ": "),
      el("ul", {}, c.check_yourself.slice(0, 4).map((r) => el("li", {}, r)))) : null,
    c.documents && c.documents.length ? el("div", { class: "small" }, el("strong", {}, t("documents") + ": "), c.documents.join(", ")) : null,
    el("div", { class: "meta" }, [c.agency, c.source_site].filter(Boolean).join(" · ")),
    el("div", { class: "actions" },
      el("a", { href: c.source_url, target: "_blank", rel: "noopener" }, t("source") + " ↗"),
      el("button", { type: "button", onclick: () => showRules(c.id) }, t("see_rules"))));
}
function section(titleKey, cards, noteKey) {
  if (!cards.length) return null;
  return el("div", { class: "section" }, el("h3", {}, t(titleKey)), noteKey ? el("p", { class: "muted small" }, t(noteKey)) : null, cards);
}
function renderResults(s) {
  const lesserIds = new Set((s.lesser_known || []).map((c) => c.id));
  const startId = s.start_with ? s.start_with.id : null;
  const others = (s.others || []).filter((c) => !lesserIds.has(c.id));
  const lesser = (s.lesser_known || []).filter((c) => c.id !== startId);
  const parts = [
    s.start_with ? section("start_with", [card(s.start_with, true)]) : el("p", { class: "muted" }, t("none_yet")),
    section("lesser_known", lesser.map((c) => card(c)), "lesser_note"),
    section("also", others.map((c) => card(c))),
    section("near", (s.near_misses || []).map((c) => card(c))),
    section("check_page", (s.check_on_official_page || []).map((c) =>
      el("article", { class: "card" }, el("h4", {}, c.name),
        c.conditions.length ? el("ul", { class: "small" }, c.conditions.map((x) => el("li", {}, x))) : null,
        el("div", { class: "actions" }, el("a", { href: c.source_url, target: "_blank", rel: "noopener" }, t("source") + " ↗"))))),
  ];
  const box = $("#results");
  box.innerHTML = "";
  box.append(...parts.filter(Boolean));
  $("#counts").textContent = T[lang].checked(s.schemes_checked, s.need_more_info);
}

async function showRules(id) {
  const r = await fetch(`/api/schemes/${encodeURIComponent(id)}`);
  if (!r.ok) return;
  const s = await r.json();
  const body = $("#rules-body");
  body.innerHTML = "";
  body.append(
    el("h3", {}, s.name),
    el("p", { class: "muted small" }, [s.agency, s.source_site].filter(Boolean).join(" · ")),
    s.verified ? null : el("p", { class: "badge unverified" }, t("not_verified")),
    s.rules.map((r) => el("div", {}, el("strong", {}, r.rule), r.quote ? el("blockquote", {}, `“${r.quote}”`) : null)),
    s.check_yourself.length ? el("div", {}, el("strong", {}, t("check_yourself")), el("ul", {}, s.check_yourself.map((x) => el("li", {}, x)))) : null,
    el("p", {}, el("a", { href: s.source_url, target: "_blank", rel: "noopener" }, s.source_url)));
  $("#rules-dialog").showModal();
}

// ---------- boot ----------
(async function boot() {
  applyLang();
  formSpec = await (await fetch("/api/form")).json();
  buildForm({});
})();
