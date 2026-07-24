"""파서 결과를 서버 없이 열 수 있는 단일 HTML 검수 화면으로 묶는다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _load_documents(output_root: Path) -> list[dict[str, Any]]:
    documents = []
    for json_path in sorted(output_root.glob("*/document.json")):
        try:
            document = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        document["_base_path"] = json_path.parent.relative_to(output_root).as_posix()
        # 화면에 쓰지 않는 절대 경로와 긴 메타데이터는 내장 데이터에서 제외한다.
        document.get("source", {}).pop("absolute_path", None)
        documents.append(document)
    return documents


def generate_review_html(output_root: Path, destination: Path | None = None) -> Path:
    output_root = output_root.resolve()
    documents = _load_documents(output_root)
    if not documents:
        raise ValueError(f"document.json 결과를 찾지 못했습니다: {output_root}")
    destination = destination.resolve() if destination else output_root / "review.html"
    payload = json.dumps(documents, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.replace("__DOCUMENT_DATA__", payload)
    destination.write_text(html, encoding="utf-8")
    return destination


TEMPLATE = r'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>교과서 PDF 구조화 검수</title>
<style>
:root{color-scheme:light;--bg:#f5f7fa;--panel:#fff;--text:#18212f;--muted:#64748b;--line:#d8dee8;--accent:#2457d6;--review:#e5484d;--ok:#16845b;--shadow:0 8px 28px rgba(31,45,61,.09)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:Pretendard,"Noto Sans KR","Malgun Gothic",sans-serif;font-size:14px}
button,select,input{font:inherit}button,select{border:1px solid var(--line);background:var(--panel);color:var(--text);border-radius:8px;padding:8px 10px}button{cursor:pointer}button:hover{border-color:var(--accent)}button:disabled{opacity:.45;cursor:not-allowed}
.app{min-height:100vh}.topbar{position:sticky;top:0;z-index:20;display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:12px 18px;background:rgba(255,255,255,.96);border-bottom:1px solid var(--line);box-shadow:0 2px 12px rgba(31,45,61,.05)}
.brand{font-weight:700;margin-right:8px}.controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.controls label{display:flex;gap:6px;align-items:center;color:var(--muted)}#document-select{max-width:370px}.page-jump{width:72px}.zoom{width:110px}.summary{margin-left:auto;color:var(--muted);white-space:nowrap}
.layout{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:16px;padding:16px;align-items:start}.workspace{min-width:0}.page-shell{max-width:1080px;margin:auto;background:var(--panel);box-shadow:var(--shadow);position:relative;line-height:0;transform-origin:top center}.page-shell img{display:block;width:100%;height:auto}.overlay{position:absolute;inset:0}.box{position:absolute;border:2px solid rgba(36,87,214,.62);background:rgba(36,87,214,.045);cursor:pointer;line-height:1.2;min-width:4px;min-height:4px}.box:hover,.box.selected{border-width:3px;background:rgba(36,87,214,.13);z-index:3}.box.review{border-color:var(--review);background:rgba(229,72,77,.07)}.box.hidden{display:none}.box-label{position:absolute;left:-2px;top:-19px;background:var(--accent);color:#fff;padding:2px 5px;border-radius:4px 4px 0 0;font-size:11px;white-space:nowrap;max-width:170px;overflow:hidden;text-overflow:ellipsis}.box.review .box-label{background:var(--review)}
.sidebar{position:sticky;top:82px;display:flex;flex-direction:column;gap:12px}.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px;box-shadow:0 3px 14px rgba(31,45,61,.04)}.card h2{font-size:15px;margin:0 0 12px}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.stat{background:var(--bg);padding:9px;border-radius:8px}.stat b{display:block;font-size:18px}.stat span{font-size:11px;color:var(--muted)}
.detail-empty{color:var(--muted);line-height:1.65}.detail-grid{display:grid;grid-template-columns:80px 1fr;gap:7px 8px;line-height:1.45}.detail-grid dt{color:var(--muted)}.detail-grid dd{margin:0;word-break:break-word}.confidence{font-variant-numeric:tabular-nums}.reason{margin-top:8px;padding:8px;background:#fff3f2;border-left:3px solid var(--review);line-height:1.45}.asset{display:block;max-width:100%;max-height:220px;margin-top:10px;border:1px solid var(--line);background:#fff}.text-preview{white-space:pre-wrap;max-height:180px;overflow:auto;background:var(--bg);padding:9px;border-radius:8px;margin-top:10px;line-height:1.5}.review-list{max-height:340px;overflow:auto;display:flex;flex-direction:column;gap:6px}.review-item{width:100%;text-align:left;padding:8px;border:1px solid var(--line);border-radius:8px;background:var(--panel);line-height:1.35}.review-item:hover{border-color:var(--review)}.review-item b{display:block}.review-item span{font-size:12px;color:var(--muted)}
.legend{display:flex;gap:12px;color:var(--muted);align-items:center}.swatch{width:13px;height:13px;border:2px solid var(--accent);display:inline-block}.swatch.review{border-color:var(--review)}.empty-page{padding:80px 20px;text-align:center;line-height:1.5;color:var(--muted)}
@media(max-width:900px){.layout{grid-template-columns:1fr}.sidebar{position:static;display:grid;grid-template-columns:1fr 1fr}.detail-card{grid-column:1/-1}.summary{width:100%;margin-left:0}.topbar{position:static}}
@media(max-width:560px){.layout{padding:8px}.topbar{padding:10px}.sidebar{display:flex}.brand{width:100%}#document-select{max-width:240px}.box-label{display:none}}
</style>
</head>
<body>
<div class="app">
  <header class="topbar">
    <div class="brand">교과서 PDF 구조화 검수</div>
    <div class="controls">
      <label>문서 <select id="document-select" aria-label="문서 선택"></select></label>
      <button id="prev-page" type="button" aria-label="이전 페이지">←</button>
      <label>페이지 <input id="page-jump" class="page-jump" type="number" min="1" aria-label="페이지 번호"> / <span id="page-total">0</span></label>
      <button id="next-page" type="button" aria-label="다음 페이지">→</button>
      <label>표시 <select id="filter-select">
        <option value="all">모든 요소</option><option value="review">확인 필요만</option>
        <option value="text">텍스트</option><option value="image">이미지·악보</option><option value="table">표</option>
      </select></label>
      <label>확대 <input id="zoom" class="zoom" type="range" min="60" max="160" value="100"> <span id="zoom-value">100%</span></label>
    </div>
    <div id="summary" class="summary"></div>
  </header>
  <main class="layout">
    <section class="workspace" aria-label="페이지 검수 화면">
      <div id="page-shell" class="page-shell"><div class="empty-page">페이지를 불러오는 중입니다.</div></div>
    </section>
    <aside class="sidebar">
      <section class="card"><h2>문서 현황</h2><div id="stats" class="stats"></div><div class="legend"><span><i class="swatch"></i> 일반</span><span><i class="swatch review"></i> 확인 필요</span></div></section>
      <section class="card"><h2>현재 페이지 확인 목록</h2><div id="review-list" class="review-list"></div></section>
      <section class="card detail-card"><h2>선택 요소</h2><div id="detail" class="detail-empty">페이지의 박스를 선택하면 텍스트, 좌표, 신뢰도와 확인 사유를 볼 수 있습니다.</div></section>
    </aside>
  </main>
</div>
<script id="document-data" type="application/json">__DOCUMENT_DATA__</script>
<script>
(() => {
  const docs = JSON.parse(document.getElementById('document-data').textContent);
  const $ = id => document.getElementById(id);
  const state = { doc: 0, page: 1, filter: 'all', selected: null, zoom: 100 };
  const typeLabels = {title:'제목',heading:'소제목',paragraph:'본문',caption:'캡션',image:'이미지',table:'표',music_score:'악보',header:'머리말',footer:'꼬리말',page_number:'쪽수',unknown:'미분류'};
  const textTypes = new Set(['title','heading','paragraph','caption','header','footer','page_number']);
  const docSelect = $('document-select');
  docs.forEach((doc, i) => { const o = document.createElement('option'); o.value=i; o.textContent=doc.source.filename; docSelect.appendChild(o); });
  const currentDoc = () => docs[state.doc];
  const currentPage = () => currentDoc().pages[state.page - 1];
  const assetUrl = path => path ? encodeURI(currentDoc()._base_path + '/' + path) : '';
  const visible = e => state.filter === 'all' || (state.filter === 'review' && e.review_required) || (state.filter === 'text' && textTypes.has(e.type)) || (state.filter === 'image' && ['image','music_score'].includes(e.type)) || state.filter === e.type;
  function render() {
    const doc = currentDoc(), page = currentPage();
    $('page-total').textContent = doc.page_count; $('page-jump').value = state.page; $('page-jump').max = doc.page_count;
    $('prev-page').disabled = state.page <= 1; $('next-page').disabled = state.page >= doc.page_count;
    $('zoom-value').textContent = state.zoom + '%';
    $('summary').textContent = `${doc.source.filename} · ${doc.review_summary.review_required_count}개 확인 필요`;
    $('stats').innerHTML = `<div class="stat"><b>${doc.page_count}</b><span>페이지</span></div><div class="stat"><b>${doc.review_summary.element_count}</b><span>요소</span></div><div class="stat"><b>${doc.review_summary.review_required_count}</b><span>확인 필요</span></div>`;
    const shell = $('page-shell'); shell.style.width = state.zoom + '%'; shell.innerHTML = '';
    if (!page || !page.render_path) { shell.innerHTML='<div class="empty-page">저장된 페이지 이미지가 없습니다.</div>'; return; }
    const img = document.createElement('img'); img.src=assetUrl(page.render_path); img.alt=`${state.page}페이지`; shell.appendChild(img);
    const overlay=document.createElement('div'); overlay.className='overlay'; shell.appendChild(overlay);
    page.elements.forEach(e => {
      const b=document.createElement('button'); b.type='button'; b.className='box'+(e.review_required?' review':'')+(visible(e)?'':' hidden')+(state.selected===e.id?' selected':'');
      const n=e.normalized_bbox; b.style.left=n[0]*100+'%'; b.style.top=n[1]*100+'%'; b.style.width=(n[2]-n[0])*100+'%'; b.style.height=(n[3]-n[1])*100+'%';
      b.setAttribute('aria-label',`${typeLabels[e.type]||e.type} ${e.id}`); b.dataset.id=e.id;
      const label=document.createElement('span'); label.className='box-label'; label.textContent=(typeLabels[e.type]||e.type)+(e.review_required?' · 확인 필요':''); b.appendChild(label);
      b.addEventListener('click',ev=>{ev.stopPropagation();state.selected=e.id;renderDetail(e);document.querySelectorAll('.box.selected').forEach(x=>x.classList.remove('selected'));b.classList.add('selected')}); overlay.appendChild(b);
    });
    renderReviews(page);
    if (!page.elements.some(e=>e.id===state.selected)) { state.selected=null; renderDetail(null); }
  }
  function renderReviews(page) {
    const list=$('review-list'), items=page.elements.filter(e=>e.review_required); list.innerHTML='';
    if(!items.length){list.innerHTML='<div class="detail-empty">이 페이지에는 확인 항목이 없습니다.</div>';return}
    items.forEach(e=>{const b=document.createElement('button');b.type='button';b.className='review-item';b.innerHTML=`<b>${typeLabels[e.type]||e.type} · ${e.id}</b><span>${e.review_reasons.map(r=>r.message).join(' / ')}</span>`;b.addEventListener('click',()=>{state.selected=e.id;renderDetail(e);document.querySelector(`[data-id="${CSS.escape(e.id)}"]`)?.scrollIntoView({behavior:'smooth',block:'center'})});list.appendChild(b)});
  }
  function renderDetail(e){const d=$('detail');if(!e){d.className='detail-empty';d.textContent='페이지의 박스를 선택하면 텍스트, 좌표, 신뢰도와 확인 사유를 볼 수 있습니다.';return}d.className='';const reasons=e.review_reasons.map(r=>`<div class="reason"><b>${r.code}</b><br>${r.message}</div>`).join('');const asset=e.asset_path?`<img class="asset" src="${assetUrl(e.asset_path)}" alt="${e.type} 추출 이미지">`:'';const text=e.text?`<div class="text-preview">${escapeHtml(e.text)}</div>`:'';d.innerHTML=`<dl class="detail-grid"><dt>유형</dt><dd>${typeLabels[e.type]||e.type}</dd><dt>ID</dt><dd>${e.id}</dd><dt>신뢰도</dt><dd class="confidence">${(e.confidence*100).toFixed(1)}%</dd><dt>추출 방식</dt><dd>${e.extraction_method}</dd><dt>좌표</dt><dd>${e.bbox.join(', ')}</dd><dt>읽기 순서</dt><dd>${e.reading_order}</dd></dl>${reasons}${asset}${text}`}
  const escapeHtml=s=>s.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));
  docSelect.addEventListener('change',()=>{state.doc=Number(docSelect.value);state.page=1;state.selected=null;render()});
  $('prev-page').addEventListener('click',()=>{if(state.page>1){state.page--;state.selected=null;render()}}); $('next-page').addEventListener('click',()=>{if(state.page<currentDoc().page_count){state.page++;state.selected=null;render()}});
  $('page-jump').addEventListener('change',e=>{state.page=Math.max(1,Math.min(currentDoc().page_count,Number(e.target.value)||1));state.selected=null;render()});
  $('filter-select').addEventListener('change',e=>{state.filter=e.target.value;render()}); $('zoom').addEventListener('input',e=>{state.zoom=Number(e.target.value);$('page-shell').style.width=state.zoom+'%';$('zoom-value').textContent=state.zoom+'%'});
  document.addEventListener('keydown',e=>{if(['INPUT','SELECT'].includes(document.activeElement.tagName))return;if(e.key==='ArrowLeft'&&state.page>1){state.page--;state.selected=null;render()}if(e.key==='ArrowRight'&&state.page<currentDoc().page_count){state.page++;state.selected=null;render()}});
  render();
})();
</script>
</body>
</html>'''


if __name__ == "__main__":
    import argparse
    command = argparse.ArgumentParser(description="PDF 파싱 결과의 HTML 검수 화면을 생성합니다.")
    command.add_argument("output", type=Path, nargs="?", default=Path("output/pdf"))
    args = command.parse_args()
    print(generate_review_html(args.output))
