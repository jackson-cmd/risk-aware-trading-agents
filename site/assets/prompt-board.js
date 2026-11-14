/* The prompt board on the leaderboard pages, drawn from assets/data/prompts.json (format rta-prompt-board-1).
   <div id="prompt-board" data-src="assets/data/prompts.json" data-lang="en"></div> */
(function () {
  var box = document.getElementById("prompt-board");
  if (!box) return;
  var zh = box.getAttribute("data-lang") === "zh";
  var T = zh ? {
    model: "模型", variant: "变体", what: "改了什么", sharpe: "Sharpe", ret: "收益", vs: "相对基线的 Sharpe（95% 区间）",
    vsret: "相对基线的收益", verdict: "结论", fmt: "格式错误", base: "基线", across: "所有模型合在一起",
    pairs: "配对", noise: "同一个提示词换种子，Sharpe 平均相差 ", per: "每个模型各自的结果", none: "还没有发布 prompt 实验。",
    verdicts: {better: "更好", worse: "更差", noise: "分不出"}
  } : {
    model: "Model", variant: "Variant", what: "What it changes", sharpe: "Sharpe", ret: "Return",
    vs: "Sharpe vs baseline (95% interval)", vsret: "Return vs baseline", verdict: "Verdict", fmt: "Format errors",
    base: "baseline", across: "All models pooled", pairs: "pairs", noise: "Two seeds of the same prompt differ in Sharpe by ",
    per: "Each model on its own", none: "No prompt lab has been published yet.", verdicts: {}
  };
  function pct(v) { return v == null ? "—" : (v * 100).toFixed(1) + "%"; }
  function n2(v) { return v == null ? "—" : Number(v).toFixed(2); }
  function el(tag, text, cls) {
    var e = document.createElement(tag);
    if (text != null) e.textContent = text;
    if (cls) e.className = cls;
    return e;
  }
  function about(v) { return (v.about || {})[zh ? "zh" : "en"] || (v.about || {}).en || ""; }
  function interval(c, f) {
    if (!c || c.diff == null) return T.base;
    var ci = c.ci95 || [];
    return f(c.diff) + " [" + f(ci[0]) + ", " + f(ci[1]) + "]";
  }
  function verdict(c) { var v = (c || {}).verdict || ""; return T.verdicts[v] || v; }
  function table(head, rows) {
    var wrap = el("div", null, "table-wrap"), t = el("table", null, "board"), tr = el("tr"), thead = el("thead"), body = el("tbody");
    head.forEach(function (h, i) { tr.appendChild(el("th", h, i > 1 ? "num" : null)); });
    thead.appendChild(tr); t.appendChild(thead);
    rows.forEach(function (cells) {
      var r = el("tr");
      cells.forEach(function (c, i) { r.appendChild(el("td", c[0], (i > 1 ? "num " : "") + (c[1] || ""))); });
      body.appendChild(r);
    });
    t.appendChild(body); wrap.appendChild(t);
    return wrap;
  }
  function boardTable(b) {
    return table([T.variant, T.what, T.sharpe, T.ret, T.vs, T.verdict, T.fmt], (b.variants || []).map(function (v) {
      var m = v.metrics || {}, c = (v.vs_baseline || {}).sharpe;
      return [[v.name], [about(v), "wrap"], [n2((m.sharpe || {}).mean)], [pct((m.total_return || {}).mean)],
              [interval(c, n2)], [verdict(c), "verdict-" + ((c || {}).verdict || "")],
              [pct((m.format_error_rate || {}).mean)]];
    }));
  }
  function noiseNote(b) {
    var n = (b.noise || {}).sharpe_mean_abs_gap_between_seeds;
    var s = b.settings || {};
    var text = T.model + ": " + b.model + ". " + (s.universe ? s.universe + ", " : "") +
      (s.periods ? (s.periods.join ? s.periods.join(", ") : s.periods) : "") +
      (s.seeds ? (zh ? "，种子 " : ", seeds ") + (s.seeds.join ? s.seeds.join(", ") : s.seeds) : "") + ".";
    if (n != null) text += " " + T.noise + n2(n) + (zh ? "。" : ".");
    if (b.lab_note) text += " " + b.lab_note;
    return el("p", text, "note");
  }
  fetch(box.getAttribute("data-src")).then(function (r) {
    if (!r.ok) throw r.status;
    return r.json();
  }).then(function (d) {
    box.textContent = "";
    var boards = d.boards || (d.variants ? [d] : []);
    if (d.across_models && (d.across_models.variants || []).length) {
      var a = d.across_models;
      box.appendChild(el("h3", T.across + " (" + (a.models || []).join(", ") + ")"));
      box.appendChild(table([T.variant, T.what, T.vs, T.vsret, T.verdict, T.pairs], a.variants.map(function (v) {
        var c = (v.vs_baseline || {}).sharpe, r = (v.vs_baseline || {}).total_return;
        return [[v.name], [about(v), "wrap"], [interval(c, n2)], [interval(r, pct)],
                [verdict(c), "verdict-" + ((c || {}).verdict || "")], [c && c.pairs != null ? String(c.pairs) : "—"]];
      })));
    }
    if (boards.length) {
      box.appendChild(el("h3", T.per));
      boards.forEach(function (b, i) {
        var det = el("details");
        if (i === 0 && !d.across_models) det.open = true;
        det.appendChild(el("summary", b.model));
        det.appendChild(noiseNote(b));
        det.appendChild(boardTable(b));
        box.appendChild(det);
      });
    }
    var m = d.method || {};
    if (m.en || m.zh) box.appendChild(el("p", zh ? (m.zh || m.en) : (m.en || m.zh), "note"));
    if (!boards.length && !d.across_models) box.appendChild(el("p", T.none, "note"));
  }).catch(function () {
    box.textContent = "";
    box.appendChild(el("p", T.none, "note"));
  });
})();
