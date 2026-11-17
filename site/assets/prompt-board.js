/* The prompt board on the leaderboard pages, drawn from assets/data/prompts.json (format rta-prompt-board-1).
   <div id="prompt-board" data-src="assets/data/prompts.json" data-lang="en"></div>
   First every variant of every lab, ranked by its Sharpe ratio against the baseline of its own lab (paired: same
   model, period and seed); then the comparison across models when there is one; then each lab's own table. */
(function () {
  var box = document.getElementById("prompt-board");
  if (!box) return;
  var zh = box.getAttribute("data-lang") === "zh";
  var T = zh ? {
    rank: "排名", variant: "变体", what: "改了什么", sharpe: "Sharpe", ret: "收益", dd: "最大回撤", held: "持股比例",
    heldBase: "持股比例（基线）", vs: "相对基线的 Sharpe（95% 区间）", vsret: "相对基线的收益（百分点，95% 区间）",
    vsdd: "相对基线的回撤（百分点）", verdict: "结论", fmt: "格式错误", bad: "无效订单", pairs: " 对", base: "基线",
    zero: "按定义为 0", ranked: "按相对基线的 Sharpe 排名",
    rankedNote: "每个变体都和同一组实验里、同一时段、同一种子的基线配对比较。回撤一栏是负数表示回撤变小。绿色和红色是校正多重比较之后仍然成立的差别；其余的和把同一个提示词再跑一遍分不开，名次只是点估计的先后。",
    across: "所有模型合在一起", per: "每组实验各自的结果", model: "模型", seeds: "，种子 ",
    noise: "同一个提示词换种子，Sharpe 平均相差 ",
    power: function (p) {
      return "每个变体只有 " + p.pairs + " 对、共 " + p.variants + " 个变体：校正之后任何差别都过不了（校正后最小可能的 p 值是 " +
        n2(p.smallest_p_holm) + "），结论按构造只能是「分不出」，数字只是估计。";
    },
    none: "还没有发布 prompt 实验。",
    verdicts: {better: "更好", worse: "更差", noise: "分不出", differs: "不同", same: "相同"}
  } : {
    rank: "#", variant: "Variant", what: "What it changes", sharpe: "Sharpe", ret: "Return", dd: "Max drawdown",
    held: "Stock held", heldBase: "Stock held (baseline)", vs: "Sharpe vs baseline (95% interval)",
    vsret: "Return vs baseline (pp, 95% interval)", vsdd: "Drawdown vs baseline (pp)", verdict: "Verdict",
    fmt: "Format errors", bad: "Invalid orders", pairs: " pairs", base: "baseline", zero: "0 by definition",
    ranked: "Ranked by Sharpe against the baseline",
    rankedNote: "Each variant is paired with the baseline of its own lab on the same period and seed. A negative drawdown difference is a smaller drawdown. Green and red mark differences that pass the correction for testing many variants; the rest cannot be told apart from running the same prompt again, and their order is the order of the point estimates.",
    across: "All models pooled", per: "Each lab on its own", model: "Model", seeds: ", seeds ",
    noise: "Two seeds of the same prompt differ in Sharpe by ",
    power: function (p) {
      return "With " + p.pairs + " pairs a variant and " + p.variants + " variants, no difference can pass the " +
        "correction (the smallest possible p-value after it is " + n2(p.smallest_p_holm) + "): every verdict is " +
        "“noise” by construction, and the numbers are estimates.";
    },
    none: "No prompt lab has been published yet.", verdicts: {}
  };
  // half away from zero (the data keep four decimals, so 7.45 must not print 7.4), and never "-0.0"
  function fixed(x, digits, plus) {
    if (x == null || isNaN(x)) return "—";
    var f = Math.pow(10, digits), s = (Math.round(Math.abs(x) * f + 1e-7) / f).toFixed(digits);
    return Number(s) === 0 ? s : (x > 0 ? (plus ? "+" : "") : "-") + s;
  }
  function pct(v) { return v == null ? "—" : fixed(v * 100, 1) + "%"; }
  function n2(v) { return fixed(v == null ? null : Number(v), 2); }
  function signed(x, digits) { return fixed(x, digits, true); }
  function sh(v) { return signed(v, 2); }
  function pp(v) { return v == null ? "—" : signed(v * 100, 1); }
  function el(tag, text, cls) {
    var e = document.createElement(tag);
    if (text != null) e.textContent = text;
    if (cls) e.className = cls;
    return e;
  }
  function lang(o) { return (o || {})[zh ? "zh" : "en"] || (o || {}).en || ""; }
  function about(v) { return lang(v.about); }
  function title(b) { return lang(b.title) || b.model; }
  function shortTitle(b) { var t = lang(b.title); return t ? t.split(/[:：]/)[0] : b.model; }
  function whole(v) { return v == null ? "—" : Math.round(v * 100) + "%"; }
  // a paired difference with its 95% interval
  function interval(c, f) {
    if (!c || c.diff == null) return T.base;
    var ci = c.ci95 || [];
    return f(c.diff) + " [" + f(ci[0]) + ", " + f(ci[1]) + "]";
  }
  function verdict(c) { var v = (c || {}).verdict || ""; return T.verdicts[v] || v; }
  function vcls(c) { return "verdict-" + ((c || {}).verdict || ""); }
  function mean(v, k) { return ((v.metrics || {})[k] || {}).mean; }
  // cols: [header, numeric?]; rows: {cls, cells: [[text or node, class]]}
  function table(cols, rows, cls) {
    var wrap = el("div", null, "table-wrap"), t = el("table", null, cls || "board"), tr = el("tr");
    var thead = el("thead"), body = el("tbody");
    cols.forEach(function (c) { tr.appendChild(el("th", c[0], c[1] ? "num" : null)); });
    thead.appendChild(tr); t.appendChild(thead);
    rows.forEach(function (row) {
      var r = el("tr", null, row.cls || null);
      row.cells.forEach(function (c, i) {
        var td = el("td", null, ((cols[i] || [])[1] ? "num " : "") + (c[1] || ""));
        if (c[0] != null && typeof c[0] === "object") td.appendChild(c[0]); else td.textContent = c[0];
        r.appendChild(td);
      });
      body.appendChild(r);
    });
    t.appendChild(body); wrap.appendChild(t);
    return wrap;
  }
  function nameCell(rank, name, sub) {
    var s = el("span");
    s.appendChild(el("span", rank + " ", "muted"));
    s.appendChild(document.createTextNode(name));
    if (sub) { s.appendChild(el("br")); s.appendChild(el("span", sub, "muted small")); }
    return s;
  }
  function baselineOf(b) {
    return (b.variants || []).filter(function (v) { return !v.vs_baseline; })[0] || {};
  }
  function ranking(boards) {
    var items = [];
    boards.forEach(function (b) {
      (b.variants || []).forEach(function (v) {
        var c = (v.vs_baseline || {}).sharpe;
        if (c && c.diff != null) items.push({b: b, v: v, c: c});
      });
    });
    if (!items.length) return null;
    items.sort(function (x, y) { return y.c.diff - x.c.diff; });
    var several = boards.length > 1, held = [], base = baselineOf(boards[0]);
    boards.forEach(function (b) {
      var e = mean(baselineOf(b), "avg_exposure");
      if (e != null && held.indexOf(Math.round(e * 100)) < 0) held.push(Math.round(e * 100));
    });
    var at = items.filter(function (x) { return x.c.diff > 0; }).length, rows = [], n = 0;
    items.splice(at, 0, null);
    items.forEach(function (x) {
      n += 1;
      if (x === null) {
        rows.push({cls: "anchor", cells: [[nameCell(n, T.base, T.zero)], [about(base), "wrap"], ["0"], ["0"], ["0"],
          [held.length ? held.join("–") + "%" : "—"], ["—"]]});
        return;
      }
      var vb = x.v.vs_baseline || {}, d = vb.max_drawdown, e = mean(x.v, "avg_exposure"), eb = mean(baselineOf(x.b), "avg_exposure");
      var sub = (several ? shortTitle(x.b) + " · " : "") + x.c.pairs + T.pairs;
      rows.push({cells: [[nameCell(n, x.v.name, sub)], [about(x.v), "wrap"], [interval(x.c, sh)],
        [interval(vb.total_return, pp)], [d ? pp(-d.diff) : "—", vcls(d)],
        [whole(e) + (eb == null ? "" : " (" + whole(eb) + ")")], [verdict(x.c), vcls(x.c)]]});
    });
    return table([[T.variant], [T.what], [T.vs, 1], [T.vsret, 1], [T.vsdd, 1], [T.heldBase, 1], [T.verdict]], rows,
      "board compare");
  }
  function boardTable(b) {
    var vs = (b.variants || []).slice();
    vs.sort(function (x, y) {
      var a = ((x.vs_baseline || {}).sharpe || {}).diff, c = ((y.vs_baseline || {}).sharpe || {}).diff;
      if (a == null) return -1;
      if (c == null) return 1;
      return c - a;
    });
    return table([[T.variant], [T.what], [T.sharpe, 1], [T.ret, 1], [T.dd, 1], [T.held, 1], [T.vs, 1], [T.verdict],
      [T.fmt, 1], [T.bad, 1]], vs.map(function (v) {
      var c = (v.vs_baseline || {}).sharpe;
      return {cls: v.vs_baseline ? null : "anchor", cells: [[v.name], [about(v), "wrap"], [n2(mean(v, "sharpe"))],
        [pct(mean(v, "total_return"))], [pct(mean(v, "max_drawdown"))], [pct(mean(v, "avg_exposure"))],
        [interval(c, sh)], [c ? verdict(c) : "—", vcls(c)], [pct(mean(v, "format_error_rate"))],
        [pct(mean(v, "invalid_order_rate"))]]};
    }), "board compare");
  }
  function labNote(b) {
    var n = (b.noise || {}).sharpe_mean_abs_gap_between_seeds, s = b.settings || {};
    var list = function (x) { return x && x.join ? x.join(", ") : x; };
    var text = T.model + ": " + b.model + ". " + (s.universe ? s.universe + ", " : "") + (list(s.periods) || "") +
      (s.seeds ? T.seeds + list(s.seeds) : "") + (zh ? "。" : ".");
    if (n != null) text += " " + T.noise + n2(n) + (zh ? "。" : ".");
    if (b.power && b.power.can_pass === false) text += " " + T.power(b.power);
    if (b.lab_note) text += " " + b.lab_note;
    return el("p", text, "note");
  }
  fetch(box.getAttribute("data-src")).then(function (r) {
    if (!r.ok) throw r.status;
    return r.json();
  }).then(function (d) {
    box.textContent = "";
    var boards = d.boards || (d.variants ? [d] : []);
    var rank = ranking(boards), notes = d.notes && lang(d.notes);
    if (rank) {
      box.appendChild(el("h3", T.ranked));
      if (notes) box.appendChild(el("p", notes, "note"));
      box.appendChild(el("p", T.rankedNote, "note"));
      box.appendChild(rank);
    }
    if (d.across_models && (d.across_models.variants || []).length) {
      var a = d.across_models;
      box.appendChild(el("h3", T.across + " (" + (a.models || []).join(", ") + ")"));
      box.appendChild(table([[T.variant], [T.what], [T.vs, 1], [T.vsret, 1], [T.verdict], [T.pairs.trim(), 1]],
        a.variants.map(function (v) {
          var c = (v.vs_baseline || {}).sharpe, r = (v.vs_baseline || {}).total_return;
          return {cls: c ? null : "anchor", cells: [[v.name], [about(v), "wrap"], [interval(c, sh)], [interval(r, pp)],
            [c ? verdict(c) : "—", vcls(c)], [c && c.pairs != null ? String(c.pairs) : "—"]]};
        }), "board compare"));
    }
    if (boards.length) {
      box.appendChild(el("h3", T.per));
      boards.forEach(function (b, i) {
        var det = el("details");
        if (i === 0 && !rank && !d.across_models) det.open = true;
        det.appendChild(el("summary", title(b)));
        det.appendChild(labNote(b));
        det.appendChild(boardTable(b));
        box.appendChild(det);
      });
    }
    if (notes && !rank) box.appendChild(el("p", notes, "note"));
    var m = d.method || {};
    if (m.en || m.zh) box.appendChild(el("p", lang(m), "note"));
    if (!boards.length && !d.across_models) box.appendChild(el("p", T.none, "note"));
  }).catch(function () {
    box.textContent = "";
    box.appendChild(el("p", T.none, "note"));
  });
})();
