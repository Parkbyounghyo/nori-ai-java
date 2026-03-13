/* ═══════════════════════════════════════
   Nori AI — PL 워크플로우 소스 카드 JS
   ═══════════════════════════════════════ */

// 소스 복사
function copySource(btn) {
  var card = btn.closest('.nori-source-card');
  var code = card.querySelector('.nori-source-box code').textContent;

  if (navigator.clipboard) {
    navigator.clipboard.writeText(code).then(function() {
      showCopyFeedback(btn);
    });
  } else {
    // fallback (SWT Browser 호환)
    var textarea = document.createElement('textarea');
    textarea.value = code;
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand('copy');
    document.body.removeChild(textarea);
    showCopyFeedback(btn);
  }
}

function showCopyFeedback(btn) {
  var icon = btn.querySelector('.copy-icon');
  if (!icon) icon = btn;
  var original = icon.textContent;
  icon.textContent = '✅';
  btn.style.color = '#4ec9b0';
  setTimeout(function() {
    icon.textContent = original;
    btn.style.color = '';
  }, 1500);
}

// 🔄 다시 생성
function onRetry(btn) {
  var card = btn.closest('.nori-source-card');
  var todoId = card.dataset.todoId || '';
  var order = card.dataset.order || '';
  btn.textContent = '⏳';
  btn.disabled = true;
  window.location.href = 'nori://retry?todoId=' + encodeURIComponent(todoId)
    + '&order=' + encodeURIComponent(order);
}

// 👍 좋아요
function onLike(btn) {
  var card = btn.closest('.nori-source-card');
  btn.classList.toggle('active-like');
  var dislikeBtn = card.querySelector('[title="안좋아요"]');
  if (dislikeBtn) dislikeBtn.classList.remove('active-dislike');

  var todoId = card.dataset.todoId || '';
  var order = card.dataset.order || '';
  var fileName = card.querySelector('.file-name') ? card.querySelector('.file-name').textContent : '';

  window.location.href = 'nori://feedback?type=like'
    + '&todoId=' + encodeURIComponent(todoId)
    + '&order=' + encodeURIComponent(order)
    + '&file=' + encodeURIComponent(fileName);
}

// 👎 안좋아요
function onDislike(btn) {
  var card = btn.closest('.nori-source-card');
  btn.classList.toggle('active-dislike');
  var likeBtn = card.querySelector('[title="좋아요"]');
  if (likeBtn) likeBtn.classList.remove('active-like');

  var reason = prompt('어떤 부분이 안 좋았나요? (선택, 빈칸 가능)');

  var todoId = card.dataset.todoId || '';
  var order = card.dataset.order || '';
  var fileName = card.querySelector('.file-name') ? card.querySelector('.file-name').textContent : '';

  window.location.href = 'nori://feedback?type=dislike'
    + '&todoId=' + encodeURIComponent(todoId)
    + '&order=' + encodeURIComponent(order)
    + '&file=' + encodeURIComponent(fileName)
    + '&reason=' + encodeURIComponent(reason || '');
}

// 의존성 완료
function onDepComplete(btn) {
  var card = btn.closest('.nori-dep-request-card');
  card.querySelector('.dep-actions').innerHTML =
    '<span style="color:#4ec9b0">⏳ 의존성 확인 후 테스트 실행 중...</span>';
  var todoId = card.dataset.todoId || '';
  var order = card.dataset.order || '';
  window.location.href = 'nori://test-with-deps?todoId=' + encodeURIComponent(todoId)
    + '&order=' + encodeURIComponent(order);
}

// 의존성 건너뛰기
function onDepSkip(btn) {
  var card = btn.closest('.nori-dep-request-card');
  card.querySelector('.dep-actions').innerHTML =
    '<span style="color:#888">⏭️ 테스트 건너뜀 — 의존성 미제공</span>';
  var todoId = card.dataset.todoId || '';
  var order = card.dataset.order || '';
  window.location.href = 'nori://test-skip?todoId=' + encodeURIComponent(todoId)
    + '&order=' + encodeURIComponent(order);
}

// ── HTML 생성 헬퍼 ──

/**
 * 소스 카드 HTML 생성
 * @param {Object} data - {fileName, filePath, startLine, source, testStatus, testReason, todoId, order}
 */
function buildSourceCard(data) {
  var testClass = 'pass';
  var testIcon = '✅';
  var testText = data.testStatus || '검증 대기';

  if (data.testStatus === 'fail') { testClass = 'fail'; testIcon = '❌'; }
  else if (data.testStatus === 'skipped') { testClass = 'skip'; testIcon = '⏭️'; }
  else if (data.testStatus === 'waiting') { testClass = 'wait'; testIcon = '⚠️'; }

  var reasonHtml = '';
  if (data.testReason) {
    reasonHtml = '<div class="nori-reason-box">' + escapeHtml(data.testReason) + '</div>';
  }

  return '<div class="nori-source-card" data-todo-id="' + (data.todoId || '') + '" data-order="' + (data.order || '') + '">'
    + '<div class="nori-file-info">'
    + '  <div class="file-name">📄 ' + escapeHtml(data.fileName) + '</div>'
    + '  <a class="file-path" href="nori://open?file=' + encodeURIComponent(data.filePath || '') + '&line=' + (data.startLine || 1) + '">'
    + '    📂 ' + escapeHtml(data.filePath || '') + '</a>'
    + '  <div class="file-line">📍 시작 라인: ' + (data.startLine || '?') + '</div>'
    + '  <div class="test-result ' + testClass + '">' + testIcon + ' ' + escapeHtml(testText) + '</div>'
    + '</div>'
    + reasonHtml
    + '<div class="nori-source-box"><pre><code>' + escapeHtml(data.source || '') + '</code></pre></div>'
    + '<div class="nori-source-actions">'
    + '  <div class="action-left">'
    + '    <button class="action-btn" onclick="onRetry(this)" title="다시 생성">🔄</button>'
    + '    <button class="action-btn" onclick="onLike(this)" title="좋아요">👍</button>'
    + '    <button class="action-btn" onclick="onDislike(this)" title="안좋아요">👎</button>'
    + '  </div>'
    + '  <div class="action-right">'
    + '    <button class="copy-btn" onclick="copySource(this)" title="소스 복사"><span class="copy-icon">📋</span></button>'
    + '  </div>'
    + '</div>'
    + '</div>';
}

/**
 * 진행 상태 HTML 생성
 * @param {Array} items - [{fileName, status, action}]
 */
function buildProgressHtml(items) {
  var html = '<div class="nori-progress">';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    var icon = '⬜';
    var cls = '';
    if (item.status === 'loading') { icon = '⏳'; cls = 'step-loading'; }
    else if (item.status === 'done' || item.status === 'suggested') { icon = '✅'; cls = 'step-done'; }
    else if (item.status === 'failed') { icon = '❌'; cls = 'step-fail'; }
    else if (item.status === 'skipped') { icon = '⏭️'; cls = 'step-skip'; }

    html += '<div class="' + cls + '">' + icon + ' ' + escapeHtml(item.fileName) + ' — ' + escapeHtml(item.action) + '</div>';
  }
  html += '</div>';
  return html;
}

function escapeHtml(text) {
  if (!text) return '';
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}
