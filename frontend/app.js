/* Tariff Calculator frontend — vanilla JS, no build step.
 *
 * Rules:
 * - Every number shown comes from the API. This file never invents a rate.
 * - Every API string is inserted via textContent / createElement — never
 *   innerHTML with API data (no raw-HTML injection from API text).
 * - An uncomputable result never shows $0.00 as a total.
 */
(function () {
  "use strict";

  var LS_KEY = "tariff_api_base";
  var DEFAULT_BASE = "http://localhost:5000";

  function $(id) { return document.getElementById(id); }

  function el(tag, text, cls) {
    var n = document.createElement(tag);
    if (text !== undefined && text !== null) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function hide(node, yes) { node.hidden = !!yes; }

  function apiBase() {
    var v = ($("apiBase").value || "").trim().replace(/\/+$/, "");
    return v || DEFAULT_BASE;
  }
  function saveBase() {
    try { localStorage.setItem(LS_KEY, apiBase()); } catch (e) { /* private mode */ }
  }

  function fmtMoney(n) {
    if (typeof n !== "number" || !isFinite(n)) return null;
    return "$" + n.toFixed(2);
  }
  function fmtScore(s) {
    if (typeof s !== "number" || !isFinite(s)) return null;
    return "Score: " + Math.round(s * 100) + "%";
  }

  function setBadge(node, text, kind) {
    node.textContent = text;
    node.className = "badge " + kind;
    hide(node, false);
  }

  // ------------------------------------------------------------------ API
  function apiFetch(path, body) {
    var url = apiBase() + path;
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (data) {
        if (!resp.ok) {
          var msg = (data && data.error && data.error.message) || ("HTTP " + resp.status);
          var err = new Error(msg);
          err.status = resp.status;
          throw err;
        }
        return data;
      });
    }).catch(function (err) {
      if (err instanceof TypeError) {
        throw new Error(
          "Could not reach the API at " + apiBase() +
          ". Is the API running? Start it with: python -m backend.app " +
          "(default http://localhost:5000), then set the API base URL above."
        );
      }
      throw err;
    });
  }

  function fetchHealth() {
    var badge = $("healthBadge");
    return fetch(apiBase() + "/v1/health").then(function (resp) {
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      return resp.json();
    }).then(function (h) {
      var ok = h && h.status === "ok" && h.index_exists;
      setBadge(
        badge,
        (ok ? "API ok" : "API degraded") + " · " + (h.revision || "?") +
        " · " + (h.chunks != null ? h.chunks.toLocaleString() : "?") + " chunks",
        ok ? "ok" : "warn"
      );
      var prov = $("provenance");
      var sha = (h.source_pdf_sha256 || "").slice(0, 12);
      clear(prov);
      prov.appendChild(document.createTextNode(
        "Provenance: " + (h.revision || "?") + " (" + (h.publication || "?") + ")" +
        " · PDF SHA-256 " + (sha ? sha + "…" : "—") +
        " · Index built " + (h.build_date || "—") +
        " · " + (h.chunks != null ? h.chunks.toLocaleString() : "?") + " chunks" +
        " · Engine: " + (h.engine || "—")
      ));
    }).catch(function () {
      setBadge(badge, "API unreachable — check API base URL", "bad");
    });
  }

  // -------------------------------------------------------------- payload
  function buildPayload() {
    var payload = {
      description: $("description").value.trim(),
      value: parseFloat($("value").value),
    };
    var hts = $("htsOverride").value.trim();
    if (hts) payload.hts_code = hts;
    var qty = $("quantity").value.trim();
    if (qty !== "") payload.quantity = parseFloat(qty);
    var unit = $("quantityUnit").value.trim();
    if (unit) payload.quantity_unit = unit;
    var origin = $("origin").value.trim();
    if (origin) payload.origin_country = origin;
    var mode = $("mode").value;
    if (mode) payload.mode = mode;
    var program = $("program").value;
    if (program) payload.program = program;
    var quota = $("quota").value;
    if (quota && quota !== "unknown") payload.quota_status = quota;
    var ch99 = $("ch99").value.split(",").map(function (s) { return s.trim(); })
      .filter(function (s) { return s; });
    if (ch99.length) payload.chapter99_stack = ch99;
    if ($("deMinimis").checked) payload.de_minimis = true;
    return payload;
  }

  function validateLocal(payload) {
    if (!payload.description || payload.description.length < 3) {
      return "Description must be at least 3 characters.";
    }
    if (!(payload.value > 0) || !isFinite(payload.value)) {
      return "Import value must be a number greater than 0.";
    }
    return null;
  }

  // -------------------------------------------------------------- render
  var lastCalc = null;
  var selectedHts = null;

  function renderWarnings(list) {
    var card = $("warningsCard");
    var ul = $("warningsList");
    clear(ul);
    (list || []).forEach(function (w) { ul.appendChild(el("li", String(w))); });
    hide(card, !(list && list.length));
  }

  function renderCalculate(data) {
    lastCalc = data;
    hide($("results"), false);
    hide($("calcError"), true);

    var match = data.match || null;
    var breakdown = data.breakdown || {};
    var computable = !!data.computable;

    // --- Best match card ---
    if (match) {
      hide($("matchCard"), false);
      $("matchHts").textContent = match.hts || "—";
      $("matchDesc").textContent = match.full_description || match.description || "—";
      var typeText = match.match_type === "exact_hts" ? "Exact HTS"
        : match.match_type === "prefix_hts" ? "HTS prefix match"
        : match.match_type ? "Best match (" + match.match_type + ")" : "Best match";
      $("matchType").textContent = typeText;
      var scoreText = fmtScore(match.score);
      if (scoreText) { $("matchScore").textContent = scoreText; hide($("matchScore"), false); }
      else hide($("matchScore"), true);
      if (match.warning) { $("matchWarning").textContent = match.warning; hide($("matchWarning"), false); }
      else hide($("matchWarning"), true);
      hide($("matchNonDutiable"), match.dutiable !== false);
      var cit = (match.citation && match.citation.citation) ||
        ((match.hts || "") + ", p." + (match.page != null ? match.page : "?"));
      $("matchCitation").textContent = "Citation: " + cit;
    } else {
      hide($("matchCard"), true);
    }

    // --- Rate card ---
    if (match) {
      hide($("rateCard"), false);
      $("rateGeneral").textContent = match.general_raw != null ? String(match.general_raw) : "—";
      $("rateSpecial").textContent = match.special_raw != null && match.special_raw !== "" ? String(match.special_raw) : "—";
      $("rateCol2").textContent = match.col2_raw != null ? String(match.col2_raw) : "—";
      var applied = data.rate_applied || null;
      var mark = function (node, on) { node.textContent = on ? "✓ Applied" : "—"; };
      mark($("appliedGeneral"), applied && applied.column === "General");
      mark($("appliedSpecial"), applied && applied.column === "Special");
      mark($("appliedCol2"), applied && applied.column === "Column 2");
      $("rowGeneral").classList.toggle("applied", !!(applied && applied.column === "General"));
      $("rowSpecial").classList.toggle("applied", !!(applied && applied.column === "Special"));
      $("rowCol2").classList.toggle("applied", !!(applied && applied.column === "Column 2"));
      var reason = "";
      if (applied && applied.reason) reason = applied.reason;
      if (applied && applied.program) reason += " Program: " + applied.program + ".";
      if (applied && applied.raw != null) reason += " Applied rate: " + applied.raw + ".";
      $("rateReason").textContent = reason || "—";
      var gsp = /\(A[\s,)]/.test(String(match.special_raw || ""));
      hide($("gspNote"), !gsp);
      $("rateUnit").textContent = match.unit ? "Rate unit (quantity): " + match.unit : "";
    } else {
      hide($("rateCard"), true);
    }

    // --- Breakdown / uncomputable ---
    if (computable) {
      hide($("breakdownCard"), false);
      hide($("uncomputableCard"), true);
      var tbody = $("breakdownRows");
      clear(tbody);
      var addRow = function (label, amount, isTotal) {
        var tr = document.createElement("tr");
        if (isTotal) tr.className = "total";
        tr.appendChild(el("td", label));
        var money = fmtMoney(amount);
        tr.appendChild(el("td", money != null ? money : "—"));
        tbody.appendChild(tr);
      };
      addRow("Base duty", breakdown.base_duty);
      (breakdown.ch99_duties || []).forEach(function (entry) {
        var label = "Chapter 99" + (entry.hts ? " " + entry.hts : "") +
          (entry.rate_raw ? " (" + entry.rate_raw + ")" : "") +
          (entry.stacking ? " — " + entry.stacking : "");
        addRow(label, entry.duty);
      });
      addRow("Merchandise Processing Fee (MPF)", breakdown.mpf);
      addRow("Harbor Maintenance Fee (HMF)", breakdown.hmf);
      addRow("Subtotal", breakdown.subtotal);
      addRow("Total estimated duty", breakdown.total, true);
    } else {
      hide($("breakdownCard"), true);
      hide($("uncomputableCard"), false);
      var warns = data.warnings || [];
      $("uncomputableWhy").textContent =
        "No duty total is shown because the matched line cannot be computed from the numbers in the HTS. " +
        (warns.length ? "API explanation: " + warns.join(" ") : "No further explanation was returned.");
      var fix = "";
      var joined = warns.join(" ").toLowerCase();
      if (joined.indexOf("quantity") !== -1) {
        fix = "What would fix it: enter a quantity and unit (see the rate unit above), then recalculate.";
      } else if (joined.indexOf("cross") !== -1 || joined.indexOf("see ") !== -1 || joined.indexOf("note") !== -1) {
        fix = "What would fix it: this line's rate is a cross-reference — check the referenced note or heading, or pick a different candidate below.";
      } else {
        fix = "What would fix it: pick a different candidate below, or enter an exact HTS override.";
      }
      $("uncomputableFix").textContent = fix;
      var cit2 = match && match.citation && match.citation.citation;
      $("uncomputableCitation").textContent = cit2 ? "Retrieved line: " + cit2 : "";
    }

    renderWarnings(data.warnings || []);

    // --- Raw JSON ---
    hide($("rawDetails"), false);
    $("rawJson").textContent = JSON.stringify(data, null, 2);
  }

  // ------------------------------------------------------------ candidates
  function fetchCandidates() {
    var desc = $("description").value.trim();
    var list = $("candidatesList");
    var card = $("candidatesCard");
    if (!desc || desc.length < 3) { hide(card, true); return; }
    hide(card, false);
    hide($("candidatesError"), true);
    hide($("candidatesLoading"), false);
    clear(list);
    apiFetch("/v1/candidates", { description: desc, top_k: 5 }).then(function (data) {
      hide($("candidatesLoading"), true);
      var cands = data.candidates || [];
      if (!cands.length) {
        list.appendChild(el("p", "No candidates returned.", "muted"));
        return;
      }
      cands.forEach(function (c) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "candidate" + (selectedHts && c.hts === selectedHts ? " selected" : "");
        var head = el("div", null, "cand-hts");
        head.textContent = (c.hts || "—") + "  ·  " + (fmtScore(c.score) || "");
        var descNode = el("div", null, "cand-desc");
        descNode.textContent = c.full_description || c.description || "";
        var meta = el("div", null, "cand-meta");
        meta.textContent = "General: " + (c.general_raw != null ? c.general_raw : "—") +
          "  ·  Special: " + (c.special_raw != null && c.special_raw !== "" ? c.special_raw : "—") +
          "  ·  Col 2: " + (c.col2_raw != null ? c.col2_raw : "—") +
          (c.warning ? "  ·  " + c.warning : "");
        btn.appendChild(head);
        btn.appendChild(descNode);
        btn.appendChild(meta);
        btn.addEventListener("click", function () {
          selectedHts = c.hts;
          $("htsOverride").value = c.hts;
          hide($("clearHts"), false);
          runCalculate();
        });
        list.appendChild(btn);
      });
    }).catch(function (err) {
      hide($("candidatesLoading"), true);
      var e = $("candidatesError");
      e.textContent = err.message;
      hide(e, false);
    });
  }

  // ------------------------------------------------------------- calculate
  function runCalculate() {
    var payload = buildPayload();
    var localErr = validateLocal(payload);
    hide($("calcError"), true);
    if (localErr) {
      var e0 = $("calcError");
      e0.textContent = localErr;
      hide(e0, false);
      return;
    }
    var btn = $("calcBtn");
    btn.disabled = true;
    hide($("calcLoading"), false);
    apiFetch("/v1/calculate", payload).then(function (data) {
      btn.disabled = false;
      hide($("calcLoading"), true);
      if (!data.match) {
        hide($("results"), false);
        hide($("matchCard"), true);
        hide($("rateCard"), true);
        hide($("breakdownCard"), true);
        hide($("uncomputableCard"), true);
        var e1 = $("calcError");
        e1.textContent = "No HTS line matched your description. Try rephrasing, or enter an exact HTS override.";
        hide(e1, false);
        renderWarnings(data.warnings || []);
        hide($("rawDetails"), false);
        $("rawJson").textContent = JSON.stringify(data, null, 2);
        return;
      }
      renderCalculate(data);
    }).catch(function (err) {
      btn.disabled = false;
      hide($("calcLoading"), true);
      var e2 = $("calcError");
      e2.textContent = err.message;
      hide(e2, false);
    });
    fetchCandidates();
  }

  // ------------------------------------------------------------------ Q&A
  function runAsk() {
    var q = $("question").value.trim();
    hide($("qaError"), true);
    if (!q || q.length < 3) {
      var e = $("qaError");
      e.textContent = "Question must be at least 3 characters.";
      hide(e, false);
      return;
    }
    var btn = $("askBtn");
    btn.disabled = true;
    hide($("qaLoading"), false);
    hide($("qaResult"), true);
    apiFetch("/v1/rag/query", { question: q, top_k: 5 }).then(function (data) {
      btn.disabled = false;
      hide($("qaLoading"), true);
      hide($("qaResult"), false);
      hide($("qaAbstain"), !data.abstained);
      $("qaAnswer").textContent = data.answer || "";
      var ul = $("qaCitations");
      clear(ul);
      (data.citations || []).forEach(function (cit) {
        ul.appendChild(el("li", cit.citation || (cit.hts || "—") + ", p." + (cit.page != null ? cit.page : "?")));
      });
      var w = $("qaWarnings");
      clear(w);
      (data.warnings || []).forEach(function (warn) {
        w.appendChild(el("p", warn, "muted"));
      });
    }).catch(function (err) {
      btn.disabled = false;
      hide($("qaLoading"), true);
      var e2 = $("qaError");
      e2.textContent = err.message;
      hide(e2, false);
    });
  }

  // ---------------------------------------------------------------- events
  $("calcBtn").addEventListener("click", function () {
    selectedHts = $("htsOverride").value.trim() || null;
    if (selectedHts) hide($("clearHts"), false);
    runCalculate();
  });
  $("clearHts").addEventListener("click", function () {
    $("htsOverride").value = "";
    selectedHts = null;
    hide($("clearHts"), true);
    runCalculate();
  });
  $("askBtn").addEventListener("click", runAsk);
  $("question").addEventListener("keydown", function (ev) {
    if (ev.key === "Enter") runAsk();
  });
  $("checkApi").addEventListener("click", function () { saveBase(); fetchHealth(); });
  $("apiBase").addEventListener("change", function () { saveBase(); fetchHealth(); });
  document.querySelectorAll("#chips .chip").forEach(function (chip) {
    chip.addEventListener("click", function () {
      $("description").value = chip.getAttribute("data-desc") || "";
      $("value").value = chip.getAttribute("data-value") || "";
      $("quantity").value = chip.getAttribute("data-qty") || "";
      $("quantityUnit").value = chip.getAttribute("data-unit") || "";
      $("htsOverride").value = "";
      selectedHts = null;
      hide($("clearHts"), true);
      runCalculate();
    });
  });

  // ------------------------------------------------------------------ init
  try {
    var saved = localStorage.getItem(LS_KEY);
    if (saved) $("apiBase").value = saved;
  } catch (e) { /* private mode */ }
  fetchHealth();
})();
