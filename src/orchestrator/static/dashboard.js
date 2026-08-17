(function () {
  function byId(id) {
    return document.getElementById(id);
  }

  var clientStatus = byId("client-status");
  var clientError = byId("client-error");

  function showClientStatus(message) {
    if (clientStatus) {
      clientStatus.textContent = message || "";
    }
  }

  function showClientError(message, error) {
    if (clientError) {
      clientError.hidden = false;
      clientError.textContent = message || "Dashboard refresh failed.";
    }
    if (clientStatus) {
      clientStatus.textContent = "Live updates unavailable.";
    }
    if (window.console && window.console.error) {
      window.console.error(error || message);
    }
  }

  function clearClientError() {
    if (clientError) {
      clientError.hidden = true;
      clientError.textContent = "";
    }
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function replaceUnderscores(value) {
    return String(value == null ? "" : value).replace(/_/g, "-");
  }

  function includes(list, value) {
    return Array.isArray(list) && list.indexOf(value) !== -1;
  }

  function zeroPad(value, width) {
    var text = String(value == null ? "" : value);
    while (text.length < width) {
      text = "0" + text;
    }
    return text;
  }

  function objectEntries(obj) {
    var result = [];
    var key;
    if (!obj) {
      return result;
    }
    for (key in obj) {
      if (Object.prototype.hasOwnProperty.call(obj, key)) {
        result.push([key, obj[key]]);
      }
    }
    return result;
  }

  function requestJson(url, options, callback) {
    var opts = options || {};
    if (window.fetch) {
      window.fetch(url, opts).then(function (response) {
        response.json().then(function (payload) {
          callback(null, payload, response);
        }).catch(function (error) {
          if (response.ok) {
            callback(error);
          } else {
            callback(null, null, response);
          }
        });
      }).catch(function (error) {
        callback(error);
      });
      return;
    }

    var xhr = new XMLHttpRequest();
    xhr.open(opts.method || "GET", url, true);
    xhr.setRequestHeader("Accept", "application/json");
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) {
        return;
      }
      try {
        callback(
          null,
          JSON.parse(xhr.responseText),
          { ok: xhr.status >= 200 && xhr.status < 300, status: xhr.status }
        );
      } catch (error) {
        if (xhr.status >= 200 && xhr.status < 300) {
          callback(error);
        } else {
          callback(null, null, { ok: false, status: xhr.status });
        }
      }
    };
    xhr.onerror = function () {
      callback(new Error("Network request failed."));
    };
    xhr.send(opts.body || null);
  }

  try {
    var initialJob = window.__ORCH_INITIAL_JOB__ || {};
    var initialJobId = window.__ORCH_INITIAL_JOB_ID__ || "";
    var summaryTitle = byId("summary-title");
    var summarySubtitle = byId("summary-subtitle");
    var metricStatus = byId("metric-status");
    var metricDoc = byId("metric-doc");
    var metricPages = byId("metric-pages");
    var metricStage = byId("metric-stage");
    var progressList = byId("progress-list");
    var pageRows = byId("page-rows");
    var logTail = byId("log-tail");
    var outputSummary = byId("output-summary");
    var outputResults = byId("output-results");
    var nameReviewCard = byId("name-review-card");
    var nameReviewStatus = byId("name-review-status");
    var nameReviewFiles = byId("name-review-files");
    var nameReviewForm = byId("name-review-form");
    var continueNameReviewButton = byId("continue-name-review");
    var jobList = byId("job-list");
    var uploadForm = byId("upload-form");
    var existingForm = byId("existing-form");
    var startUploadButton = byId("start-upload");
    var startExistingButton = byId("start-existing");
    var resumeButton = byId("resume-job");
    var pauseButton = byId("pause-job");
    var cancelButton = byId("cancel-job");
    var clearResultsButton = byId("clear-results");
    var actionFeedback = byId("action-feedback");
    var actionFeedbackTitle = byId("action-feedback-title");
    var actionFeedbackDetail = byId("action-feedback-detail");
    var actionProgressTrack = byId("action-progress-track");
    var actionProgressFill = byId("action-progress-fill");
    var currentJobId = initialJobId;
    var currentPayload = initialJob || {};
    var source = null;
    var sourceJobId = "";
    var streamConnected = false;
    var pollHandle = null;
    var reconnectHandle = null;
    var reconnectAttempts = 0;
    var refreshHandle = null;
    var refreshInFlight = false;
    var refreshQueued = false;
    var lastJobsRefreshAt = 0;
    var actionVersion = 0;
    var actionHideHandle = null;

    var stageOrder = ["ingest", "ocr", "classify", "names", "meta", "places", "aggregate"];
    var stageLabels = {
      ingest: "Ingest",
      ocr: "OCR",
      classify: "Classify",
      names: "Names",
      meta: "Metadata",
      places: "Places",
      aggregate: "Aggregate"
    };

    function statusUrl(jobId) {
      return window.__ORCH_STATUS_URL__.replace("__JOB_ID__", encodeURIComponent(jobId));
    }

    function streamUrl(jobId) {
      return window.__ORCH_STREAM_URL__.replace("__JOB_ID__", encodeURIComponent(jobId));
    }

    function resumeUrl(docId) {
      return window.__ORCH_RESUME_URL__.replace("__DOC_ID__", encodeURIComponent(docId));
    }

    function pauseUrl(jobId) {
      return window.__ORCH_PAUSE_URL__.replace("__JOB_ID__", encodeURIComponent(jobId));
    }

    function cancelUrl(jobId) {
      return window.__ORCH_CANCEL_URL__.replace("__JOB_ID__", encodeURIComponent(jobId));
    }

    function clearResultsUrl(docId) {
      return window.__ORCH_CLEAR_RESULTS_URL__.replace("__DOC_ID__", encodeURIComponent(docId));
    }

    function outputsUrl(jobId) {
      return window.__ORCH_OUTPUTS_URL__.replace("__JOB_ID__", encodeURIComponent(jobId));
    }

    function continueNameReviewUrl(jobId) {
      return window.__ORCH_CONTINUE_NAME_REVIEW_URL__.replace("__JOB_ID__", encodeURIComponent(jobId));
    }

    function isActiveStatus(status) {
      return includes(["pending", "running", "cancelling", "pausing"], status || "");
    }

    function isTerminalStatus(status) {
      return includes(["done", "done_with_errors", "failed", "cancelled", "paused", "awaiting_name_review"], status || "");
    }

    function isButtonBusy(button) {
      return Boolean(button && button.getAttribute("aria-busy") === "true");
    }

    function setButtonBusy(button, busy, label) {
      if (!button) {
        return;
      }
      if (busy) {
        if (!button.dataset.idleLabel) {
          button.dataset.idleLabel = button.textContent;
        }
        button.textContent = label || "Working...";
        button.disabled = true;
        button.classList.add("is-busy");
        button.setAttribute("aria-busy", "true");
        return;
      }
      button.textContent = button.dataset.idleLabel || button.textContent;
      button.classList.remove("is-busy");
      button.removeAttribute("aria-busy");
      button.disabled = false;
    }

    function showActionFeedback(title, detail, percent) {
      var hasPercent = percent !== null && percent !== undefined && percent !== "";
      var numericPercent = hasPercent ? Number(percent) : NaN;
      if (!actionFeedback) {
        return;
      }
      actionFeedback.hidden = false;
      if (actionFeedbackTitle) {
        actionFeedbackTitle.textContent = title || "Working...";
      }
      if (actionFeedbackDetail) {
        actionFeedbackDetail.textContent = detail || "";
      }
      if (!actionProgressTrack || !actionProgressFill) {
        return;
      }
      if (hasPercent && isFinite(numericPercent) && numericPercent >= 0) {
        numericPercent = Math.max(0, Math.min(100, Math.round(numericPercent)));
        actionProgressTrack.classList.remove("is-indeterminate");
        actionProgressTrack.setAttribute("aria-valuemin", "0");
        actionProgressTrack.setAttribute("aria-valuemax", "100");
        actionProgressTrack.setAttribute("aria-valuenow", String(numericPercent));
        actionProgressFill.style.width = numericPercent + "%";
      } else {
        actionProgressTrack.classList.add("is-indeterminate");
        actionProgressTrack.removeAttribute("aria-valuenow");
        actionProgressFill.style.width = "";
      }
    }

    function beginAction(button, title, detail, buttonLabel) {
      actionVersion += 1;
      if (actionHideHandle) {
        window.clearTimeout(actionHideHandle);
        actionHideHandle = null;
      }
      setButtonBusy(button, true, buttonLabel);
      showActionFeedback(title, detail, null);
      return actionVersion;
    }

    function finishAction(button, token, title, detail, keepVisible) {
      setButtonBusy(button, false);
      showActionFeedback(title, detail, 100);
      if (!keepVisible) {
        actionHideHandle = window.setTimeout(function () {
          if (actionFeedback && token === actionVersion) {
            actionFeedback.hidden = true;
          }
        }, 2600);
      }
    }

    function failAction(button, title, detail) {
      setButtonBusy(button, false);
      showActionFeedback(title || "Request failed", detail || "Check the error below and try again.", 0);
    }

    function setLaunchControlsDisabled(disabled, activeButton) {
      var buttons = [startUploadButton, startExistingButton];
      var index;
      for (index = 0; index < buttons.length; index += 1) {
        if (!buttons[index]) {
          continue;
        }
        if (disabled) {
          buttons[index].disabled = true;
        } else if (buttons[index] !== activeButton || !isButtonBusy(buttons[index])) {
          buttons[index].disabled = false;
        }
      }
    }

    function formatBytes(value) {
      var bytes = Number(value || 0);
      if (bytes < 1024) {
        return Math.round(bytes) + " B";
      }
      if (bytes < 1024 * 1024) {
        return (bytes / 1024).toFixed(1) + " KB";
      }
      return (bytes / (1024 * 1024)).toFixed(1) + " MB";
    }

    function stageStateClass(state) {
      return "state-" + replaceUnderscores(state || "pending");
    }

    function countStage(payload, stage) {
      var pages = payload.pages || [];
      var counts = { total: 0, done: 0, running: 0, failed: 0, skipped: 0 };
      var aggregateState;
      var index;
      var state;

      if (stage === "aggregate") {
        aggregateState = ((payload.aggregate || {}).state) || "pending";
        counts.total = pages.length || Number(payload.total_pages || 0);
        counts.done = aggregateState === "done" ? counts.total : 0;
        counts.running = aggregateState === "running" ? 1 : 0;
        counts.failed = aggregateState === "failed" ? 1 : 0;
        counts.skipped = aggregateState === "skipped" ? counts.total : 0;
        return counts;
      }

      counts.total = pages.length;
      for (index = 0; index < pages.length; index += 1) {
        state = ((pages[index][stage] || {}).state) || "pending";
        if (state === "done") {
          counts.done += 1;
        } else if (state === "running") {
          counts.running += 1;
        } else if (state === "failed") {
          counts.failed += 1;
        } else if (state === "skipped") {
          counts.skipped += 1;
        }
      }
      return counts;
    }

    function renderProgress(payload) {
      var pages = payload.pages || [];
      var html = "";
      var index;
      var stage;
      var counts;
      var total;
      var completed;
      var percent;
      if (!pages.length) {
        progressList.innerHTML = '<div class="empty">No stage progress yet.</div>';
        return;
      }
      for (index = 0; index < stageOrder.length; index += 1) {
        stage = stageOrder[index];
        counts = countStage(payload, stage);
        total = counts.total || 0;
        completed = counts.done + counts.skipped;
        percent = total ? Math.round((completed / total) * 100) : 0;
        html += '<div class="progress-row">';
        html += '<div class="progress-meta">';
        html += '<strong>' + escapeHtml(stageLabels[stage]) + '</strong>';
        html += '<span>' + completed + '/' + total + ' complete' + (counts.failed ? ', ' + counts.failed + ' failed' : '') + '</span>';
        html += '</div>';
        html += '<div class="progress-track"><div class="progress-fill" style="width:' + percent + '%"></div></div>';
        html += '</div>';
      }
      progressList.innerHTML = html;
    }

    function renderStatusCell(page, stage) {
      var info = page[stage] || {};
      var href = (page.links || {})[stage] || "#";
      var label = info.state || "pending";
      var detail = info.detail || info.error || "";
      return '<a class="status-pill ' + stageStateClass(label) + '" href="' + escapeHtml(href) + '" title="' + escapeHtml(detail) + '" target="_blank" rel="noreferrer">' + escapeHtml(label) + '</a>';
    }

    function renderMetaPlacesCell(page) {
      return '<div class="meta-place">' + renderStatusCell(page, "meta") + renderStatusCell(page, "places") + '</div>';
    }

    function renderRows(payload) {
      var pages = payload.pages || [];
      var html = "";
      var index;
      var page;
      if (!pages.length) {
        pageRows.innerHTML = "<tr><td colspan='8'>No page rows yet.</td></tr>";
        return;
      }
      for (index = 0; index < pages.length; index += 1) {
        page = pages[index];
        html += "<tr>";
        html += '<td><a class="page-link" href="' + escapeHtml(((page.links || {}).ocr) || "#") + '" target="_blank" rel="noreferrer">p' + zeroPad(page.page, 3) + "</a></td>";
        html += "<td>" + renderStatusCell(page, "ingest") + "</td>";
        html += "<td>" + renderStatusCell(page, "ocr") + "</td>";
        html += "<td>" + renderStatusCell(page, "classify") + "</td>";
        html += "<td>" + renderStatusCell(page, "names") + "</td>";
        html += "<td>" + renderMetaPlacesCell(page) + "</td>";
        html += "<td>" + renderStatusCell(page, "aggregate") + "</td>";
        html += '<td class="note-cell">' + escapeHtml(page.note || "") + "</td>";
        html += "</tr>";
      }
      pageRows.innerHTML = html;
    }

    function renderLog(payload) {
      var lines = payload.log_tail || [];
      logTail.textContent = lines.length ? lines.join("\n") : "No log output yet.";
    }

    function renderOutputSummary(summary) {
      var data;
      var stats;
      var metrics;
      var rawMetrics;
      var authority;
      var cleanup;
      var html = "";
      var index;

      if (!outputSummary) {
        return;
      }
      if (!summary || !summary.exists || !summary.parse_ok) {
        outputSummary.innerHTML = '<div class="empty">No aggregation summary yet.</div>';
        return;
      }
      data = summary.data || {};
      stats = data.stats || {};
      rawMetrics = objectEntries(stats);
      metrics = [];
      for (index = 0; index < rawMetrics.length && metrics.length < 8; index += 1) {
        if (rawMetrics[index][1] == null || typeof rawMetrics[index][1] !== "object") {
          metrics.push(rawMetrics[index]);
        }
      }
      authority = stats.authoritative_name_review || {};
      cleanup = Array.isArray(data.cleanup_actions) ? data.cleanup_actions.slice(0, 6) : [];

      if (authority.enabled) {
        html += '<div class="review-verification">';
        html += '<strong>Reviewed roster verified</strong>';
        html += '<span>Expected ' + escapeHtml(authority.expected_name_page_pairs || 0);
        html += ' &middot; Detail ' + escapeHtml(authority.detail_name_page_pairs || 0);
        html += ' &middot; Places ' + escapeHtml(authority.place_name_page_pairs || 0) + '</span>';
        html += '</div>';
      }

      html += '<div class="summary-metrics">';
      if (!metrics.length) {
        html += '<div class="empty">No summary stats yet.</div>';
      } else {
        for (index = 0; index < metrics.length; index += 1) {
          html += '<div class="metric mini"><span>' + escapeHtml(metrics[index][0]) + '</span><strong>' + escapeHtml(metrics[index][1]) + '</strong></div>';
        }
      }
      html += "</div>";

      if (cleanup.length) {
        html += '<div class="cleanup-list"><strong>Cleanup Actions</strong><ul>';
        for (index = 0; index < cleanup.length; index += 1) {
          html += "<li>" + escapeHtml(cleanup[index]) + "</li>";
        }
        html += "</ul></div>";
      }
      outputSummary.innerHTML = html;
    }

    function renderOutputFile(file) {
      var missing = !file.exists;
      var headers = file.headers || [];
      var rows = file.rows || [];
      var html = "";
      var rowIndex;
      var headerIndex;

      html += '<section class="output-file ' + (missing ? "missing" : "") + '">';
      html += '<div class="output-file-head"><div>';
      html += "<h3>" + escapeHtml(file.label || file.key || "Output") + "</h3>";
      html += "<p>" + (missing ? "File not written yet." : escapeHtml(file.row_count || 0) + " previewed row(s)") + "</p>";
      html += "</div>";
      if (!missing) {
        html += '<a class="download-link" href="' + escapeHtml(file.download_url || "#") + '">Download</a>';
      }
      html += "</div>";

      if (!headers.length) {
        html += '<div class="empty">No preview available yet.</div>';
      } else {
        html += '<div class="output-table-wrap"><table class="mini-table"><thead><tr>';
        for (headerIndex = 0; headerIndex < headers.length; headerIndex += 1) {
          html += "<th>" + escapeHtml(headers[headerIndex]) + "</th>";
        }
        html += "</tr></thead><tbody>";
        if (!rows.length) {
          html += '<tr><td colspan="' + headers.length + '">No rows yet.</td></tr>';
        } else {
          for (rowIndex = 0; rowIndex < rows.length; rowIndex += 1) {
            html += "<tr>";
            for (headerIndex = 0; headerIndex < headers.length; headerIndex += 1) {
              html += "<td>" + escapeHtml(rows[rowIndex][headers[headerIndex]]) + "</td>";
            }
            html += "</tr>";
          }
        }
        html += "</tbody></table></div>";
      }

      if (file.preview_truncated) {
        html += '<p class="preview-note">Preview truncated to the first few rows.</p>';
      }
      html += "</section>";
      return html;
    }

    function renderOutputs(payload) {
      var files = payload.files || [];
      var html = "";
      var index;
      if (!outputResults || !outputSummary) {
        return;
      }
      renderOutputSummary(payload.summary || {});
      if (!files.length) {
        outputResults.innerHTML = '<div class="empty">No final outputs yet.</div>';
        return;
      }
      for (index = 0; index < files.length; index += 1) {
        html += renderOutputFile(files[index]);
      }
      outputResults.innerHTML = html;
    }

    function renderNameReview(payload) {
      var review = payload.name_review || {};
      var files = review.files || [];
      var show = Boolean(review.status || files.length || payload.status === "awaiting_name_review");
      var html = "";
      var index;
      var file;
      var canContinue = payload.status === "awaiting_name_review" || review.status === "ready";

      if (!nameReviewCard) {
        return;
      }
      nameReviewCard.hidden = !show;
      if (!show) {
        return;
      }
      if (nameReviewStatus) {
        nameReviewStatus.textContent = review.message || "Review extracted subject names before metadata, places, and aggregation continue.";
      }
      if (nameReviewFiles) {
        if (!files.length) {
          html = '<div class="empty">No name review files yet.</div>';
        } else {
          for (index = 0; index < files.length; index += 1) {
            file = files[index];
            html += '<section class="review-file">';
            html += '<div><strong>' + escapeHtml(file.label || file.key) + '</strong>';
            html += '<span>' + escapeHtml(file.size_bytes || 0) + ' bytes</span></div>';
            html += '<a class="download-link" href="' + escapeHtml(file.download_url || "#") + '">Download</a>';
            html += '</section>';
          }
        }
        nameReviewFiles.innerHTML = html;
      }
      if (continueNameReviewButton) {
        continueNameReviewButton.disabled = isButtonBusy(continueNameReviewButton) || !currentJobId || !canContinue;
      }
      if (nameReviewForm) {
        nameReviewForm.classList.toggle("muted", !canContinue);
      }
    }

    function renderJobList(jobs) {
      var html = "";
      var index;
      var job;
      if (!jobList) {
        return;
      }
      if (!jobs.length) {
        jobList.innerHTML = '<p class="empty">No jobs yet.</p>';
        return;
      }
      for (index = 0; index < jobs.length; index += 1) {
        job = jobs[index];
        html += '<a class="job-pill ' + (job.job_id === currentJobId ? "active" : "") + '" href="' + window.__ORCH_INDEX_URL__ + '?job_id=' + encodeURIComponent(job.job_id) + '">';
        html += "<strong>" + escapeHtml(job.doc_id) + "</strong>";
        html += "<span>" + escapeHtml(job.status || "pending") + "</span>";
        html += "</a>";
      }
      jobList.innerHTML = html;
    }

    function syncControls(payload) {
      var status = payload.status || "";
      var active = isActiveStatus(status);
      var canPause = includes(["pending", "running"], status);
      var canCancel = includes(["pending", "running", "pausing"], status);
      var docId = payload.doc_id || "";
      var jobId = payload.job_id || "";

      if (resumeButton) {
        resumeButton.dataset.docId = docId;
        resumeButton.disabled = isButtonBusy(resumeButton) || !docId || active || status === "awaiting_name_review";
      }
      if (pauseButton) {
        pauseButton.dataset.jobId = jobId;
        pauseButton.disabled = isButtonBusy(pauseButton) || !jobId || !canPause;
      }
      if (cancelButton) {
        cancelButton.dataset.jobId = jobId;
        cancelButton.disabled = isButtonBusy(cancelButton) || !jobId || !canCancel;
      }
      if (clearResultsButton) {
        clearResultsButton.dataset.docId = docId;
        clearResultsButton.disabled = isButtonBusy(clearResultsButton) || !docId || active;
      }
    }

    function syncHistory(jobId) {
      if (!jobId || !window.history || !window.history.replaceState) {
        return;
      }
      window.history.replaceState({}, "", window.__ORCH_INDEX_URL__ + "?job_id=" + encodeURIComponent(jobId));
    }

    function render(payload) {
      currentPayload = payload || {};
      currentJobId = payload.job_id || currentJobId;
      if (summaryTitle) {
        summaryTitle.textContent = payload.doc_id ? "Current Job - " + payload.doc_id : "Current Job";
      }
      if (summarySubtitle) {
        summarySubtitle.textContent = payload.job_id ? "Job " + payload.job_id : "Select or start a job to load pipeline progress.";
      }
      if (metricStatus) {
        metricStatus.textContent = payload.status || "-";
      }
      if (metricDoc) {
        metricDoc.textContent = payload.doc_id || "-";
      }
      if (metricPages) {
        metricPages.textContent = String(payload.total_pages || 0);
      }
      if (metricStage) {
        metricStage.textContent = payload.current_stage || "-";
      }
      renderProgress(payload);
      renderRows(payload);
      renderLog(payload);
      renderNameReview(payload);
      syncControls(payload);
      syncTransport();
      syncHistory(currentJobId);
    }

    function refreshJobs(done) {
      if (!jobList) {
        if (done) {
          done();
        }
        return;
      }
      lastJobsRefreshAt = Date.now();
      requestJson(window.__ORCH_JOBS_URL__, null, function (error, payload) {
        if (error) {
          if (done) {
            done();
          }
          return;
        }
        if (payload && payload.jobs) {
          renderJobList(payload.jobs || []);
        }
        if (done) {
          done();
        }
      });
    }

    function maybeRefreshJobs(force) {
      if (force || !lastJobsRefreshAt || Date.now() - lastJobsRefreshAt >= 10000) {
        refreshJobs();
      }
    }

    function refreshOutputs(jobId, done) {
      if (!outputResults || !outputSummary) {
        if (done) {
          done();
        }
        return;
      }
      if (!jobId) {
        renderOutputs({ files: [], summary: {} });
        if (done) {
          done();
        }
        return;
      }
      requestJson(outputsUrl(jobId), null, function (error, payload, response) {
        if (error || !response || !response.ok) {
          renderOutputs({ files: [], summary: {} });
          if (done) {
            done();
          }
          return;
        }
        renderOutputs(payload || { files: [], summary: {} });
        if (done) {
          done();
        }
      });
    }

    function refresh(jobId, done) {
      var previousStatus = currentPayload.status || "";
      if (!jobId) {
        if (done) {
          done();
        }
        return;
      }
      requestJson(statusUrl(jobId), null, function (error, payload, response) {
        if (error) {
          showClientError("Could not refresh dashboard status.", error);
          if (done) {
            done();
          }
          return;
        }
        if (!response || !response.ok || !payload) {
          if (done) {
            done();
          }
          return;
        }
        if (jobId !== currentJobId) {
          if (done) {
            done();
          }
          return;
        }
        clearClientError();
        render(payload);
        maybeRefreshJobs(previousStatus !== (payload.status || ""));
        if (isTerminalStatus(payload.status) && previousStatus !== payload.status) {
          refreshOutputs(payload.job_id);
        }
        if (done) {
          done();
        }
      });
    }

    function scheduleRefresh(jobId, delay) {
      if (!jobId || jobId !== currentJobId) {
        return;
      }
      refreshQueued = true;
      if (refreshHandle || refreshInFlight) {
        return;
      }
      refreshHandle = window.setTimeout(function () {
        var targetJobId = currentJobId;
        refreshHandle = null;
        if (!refreshQueued || !targetJobId) {
          return;
        }
        refreshQueued = false;
        refreshInFlight = true;
        refresh(targetJobId, function () {
          refreshInFlight = false;
          if (refreshQueued && currentJobId) {
            scheduleRefresh(currentJobId, 200);
          }
        });
      }, typeof delay === "number" ? delay : 250);
    }

    function stopPolling() {
      if (pollHandle) {
        window.clearInterval(pollHandle);
        pollHandle = null;
      }
    }

    function stopReconnect() {
      if (reconnectHandle) {
        window.clearTimeout(reconnectHandle);
        reconnectHandle = null;
      }
    }

    function syncPolling() {
      var status = currentPayload.status || "";
      var shouldPoll = Boolean(currentJobId) && isActiveStatus(status) && !streamConnected;
      if (!shouldPoll) {
        stopPolling();
        return;
      }
      if (!pollHandle) {
        pollHandle = window.setInterval(function () {
          if (currentJobId) {
            scheduleRefresh(currentJobId, 0);
          }
        }, 3000);
      }
    }

    function scheduleReconnect(jobId) {
      var delay;
      stopReconnect();
      if (currentJobId !== jobId || !isActiveStatus(currentPayload.status)) {
        return;
      }
      delay = Math.min(15000, 1000 * Math.pow(2, reconnectAttempts));
      reconnectAttempts += 1;
      reconnectHandle = window.setTimeout(function () {
        if (currentJobId === jobId && isActiveStatus(currentPayload.status)) {
          connect(jobId);
        }
      }, delay);
    }

    function closeSource() {
      if (source) {
        source.close();
      }
      source = null;
      sourceJobId = "";
      streamConnected = false;
    }

    function syncTransport() {
      if (!currentJobId || !isActiveStatus(currentPayload.status)) {
        stopReconnect();
        closeSource();
        stopPolling();
        return;
      }
      syncPolling();
    }

    function connect(jobId) {
      var eventSource;
      var eventNames;
      var index;
      if (!jobId) {
        showClientStatus("No live job selected.");
        return;
      }
      if (jobId !== currentJobId || !isActiveStatus(currentPayload.status)) {
        syncTransport();
        return;
      }
      if (source && sourceJobId === jobId) {
        return;
      }
      closeSource();
      if (!window.EventSource) {
        showClientStatus("Using polling fallback for live updates.");
        syncPolling();
        return;
      }
      eventSource = new EventSource(streamUrl(jobId));
      source = eventSource;
      sourceJobId = jobId;
      eventSource.onopen = function () {
        if (source !== eventSource) {
          return;
        }
        streamConnected = true;
        reconnectAttempts = 0;
        stopPolling();
        clearClientError();
        showClientStatus("Live updates connected.");
      };
      eventNames = ["snapshot", "status", "page_updated", "log", "pause_requested", "cancel_requested", "done"];
      for (index = 0; index < eventNames.length; index += 1) {
        eventSource.addEventListener(eventNames[index], function () {
          scheduleRefresh(jobId, 200);
        });
      }
      eventSource.onerror = function () {
        if (source !== eventSource) {
          return;
        }
        closeSource();
        if (!isActiveStatus(currentPayload.status)) {
          stopReconnect();
          stopPolling();
          return;
        }
        showClientStatus("Live stream disconnected. Retrying...");
        syncPolling();
        scheduleReconnect(jobId);
      };
    }

    function activateJob(payload) {
      var nextJobId = (payload || {}).job_id || "";
      if (!nextJobId) {
        return;
      }
      if (currentJobId !== nextJobId) {
        stopReconnect();
        closeSource();
        stopPolling();
      }
      currentJobId = nextJobId;
      currentPayload = payload;
      render(payload);
      maybeRefreshJobs(true);
      if (isTerminalStatus(payload.status)) {
        refreshOutputs(nextJobId);
      } else {
        connect(nextJobId);
        scheduleRefresh(nextJobId, 250);
      }
    }

    function postFormWithProgress(form, url, onProgress, callback) {
      var xhr = new XMLHttpRequest();
      var body = new FormData(form);
      xhr.open("POST", url, true);
      xhr.setRequestHeader("Accept", "application/json");
      if (xhr.upload && onProgress) {
        xhr.upload.onprogress = function (event) {
          if (event.lengthComputable && event.total > 0) {
            onProgress(Math.round((event.loaded / event.total) * 100), event.loaded, event.total);
          }
        };
        xhr.upload.onload = function () {
          onProgress(null, 0, 0);
        };
      }
      xhr.onload = function () {
        var payload = null;
        try {
          payload = xhr.responseText ? JSON.parse(xhr.responseText) : null;
        } catch (error) {
          if (xhr.status >= 200 && xhr.status < 300) {
            callback(error, null, { ok: false, status: xhr.status });
            return;
          }
        }
        callback(null, payload, { ok: xhr.status >= 200 && xhr.status < 300, status: xhr.status });
      };
      xhr.onerror = function () {
        callback(new Error("Network request failed."), null, { ok: false, status: 0 });
      };
      xhr.send(body);
    }

    function submitRunForm(form, button, uploadTitle) {
      var token = beginAction(button, uploadTitle, "Preparing request...", "Starting...");
      setLaunchControlsDisabled(true, button);
      postFormWithProgress(
        form,
        window.__ORCH_RUN_URL__,
        function (percent, loaded, total) {
          if (percent == null) {
            showActionFeedback("Starting pipeline", "Upload received; saving the PDF and creating the job...", null);
          } else {
            showActionFeedback(uploadTitle, formatBytes(loaded) + " of " + formatBytes(total), percent);
          }
        },
        function (error, payload, response) {
          setButtonBusy(button, false);
          setLaunchControlsDisabled(false, button);
          if (response && response.status === 409 && payload) {
            finishAction(button, token, "Job already active", "Switched to the existing active job.", false);
            activateJob(payload);
            return;
          }
          if (error || !response || !response.ok || !payload) {
            failAction(button, "Run request failed", "The job was not started. Check the error message and try again.");
            showClientError("Run request failed.", error || new Error("Run request failed."));
            return;
          }
          clearClientError();
          finishAction(button, token, "Pipeline started", "Live status is loading now.", false);
          activateJob(payload);
        }
      );
    }

    function postEmpty(url, callback) {
      requestJson(url, { method: "POST" }, function (error, payload, response) {
        if (callback) {
          callback(error, payload, response);
        }
      });
    }

    if (uploadForm) {
      uploadForm.addEventListener("submit", function (event) {
        event.preventDefault();
        if (isButtonBusy(startUploadButton)) {
          return;
        }
        submitRunForm(uploadForm, startUploadButton, "Uploading PDF");
      });
    }

    if (existingForm) {
      existingForm.addEventListener("submit", function (event) {
        event.preventDefault();
        if (isButtonBusy(startExistingButton)) {
          return;
        }
        submitRunForm(existingForm, startExistingButton, "Starting registered PDF");
      });
    }

    if (resumeButton) {
      resumeButton.addEventListener("click", function () {
        var token;
        if (!resumeButton.dataset.docId) {
          return;
        }
        token = beginAction(resumeButton, "Resuming pipeline", "Creating a resumable worker...", "Resuming...");
        postEmpty(resumeUrl(resumeButton.dataset.docId), function (error, payload, response) {
          if (response && response.status === 409 && payload) {
            finishAction(resumeButton, token, "Job already active", "Switched to the existing active job.", false);
            activateJob(payload);
            return;
          }
          if (error || !response || !response.ok || !payload) {
            failAction(resumeButton, "Resume request failed", "The pipeline was not resumed.");
            syncControls(currentPayload);
            showClientError("Resume request failed.", error || new Error("Resume request failed."));
            return;
          }
          clearClientError();
          finishAction(resumeButton, token, "Pipeline resumed", "Live status is loading now.", false);
          activateJob(payload);
        });
      });
    }

    if (pauseButton) {
      pauseButton.addEventListener("click", function () {
        var jobId = pauseButton.dataset.jobId;
        var previousStatus = currentPayload.status || "";
        var token;
        if (!jobId) {
          return;
        }
        token = beginAction(pauseButton, "Requesting pause", "The active page will finish before the worker pauses.", "Requesting...");
        currentPayload.status = "pausing";
        render(currentPayload);
        postEmpty(pauseUrl(jobId), function (error, payload, response) {
          if (error || !response || !response.ok || !payload) {
            currentPayload.status = previousStatus;
            failAction(pauseButton, "Pause request failed", "The job is still running.");
            render(currentPayload);
            showClientError("Pause request failed.", error || new Error("Pause request failed."));
            return;
          }
          clearClientError();
          finishAction(pauseButton, token, "Pause requested", "The active page will finish before the worker pauses.", false);
          render(payload);
          showClientStatus("Pause request accepted; waiting for the active page to finish.");
        });
      });
    }

    if (cancelButton) {
      cancelButton.addEventListener("click", function () {
        var jobId = cancelButton.dataset.jobId;
        var previousStatus = currentPayload.status || "";
        var token;
        if (!jobId) {
          return;
        }
        token = beginAction(cancelButton, "Requesting cancellation", "The active page will finish before the worker stops.", "Requesting...");
        currentPayload.status = "cancelling";
        render(currentPayload);
        postEmpty(cancelUrl(jobId), function (error, payload, response) {
          if (error || !response || !response.ok || !payload) {
            currentPayload.status = previousStatus;
            failAction(cancelButton, "Cancel request failed", "The job is still running.");
            render(currentPayload);
            showClientError("Cancel request failed.", error || new Error("Cancel request failed."));
            return;
          }
          clearClientError();
          finishAction(cancelButton, token, "Cancellation requested", "The active page will finish before the worker stops.", false);
          render(payload);
          showClientStatus("Cancellation accepted; waiting for the active page to finish.");
        });
      });
    }

    if (clearResultsButton) {
      clearResultsButton.addEventListener("click", function () {
        var confirmed;
        var token;
        if (!clearResultsButton.dataset.docId) {
          return;
        }
        confirmed = window.confirm("Clear all generated results for " + clearResultsButton.dataset.docId + "? This keeps the source PDF but removes pages, OCR text, intermediate JSON, outputs, logs, and audit artifacts.");
        if (!confirmed) {
          return;
        }
        token = beginAction(clearResultsButton, "Clearing generated results", "The source PDF will be kept.", "Clearing...");
        postEmpty(clearResultsUrl(clearResultsButton.dataset.docId), function (error, payload, response) {
          if (error || !response || !response.ok) {
            failAction(clearResultsButton, "Clear request failed", "No confirmation was received from the server.");
            syncControls(currentPayload);
            showClientError("Clear results request failed.", error || new Error("Clear results request failed."));
            return;
          }
          finishAction(clearResultsButton, token, "Results cleared", "Reloading the dashboard...", true);
          window.location.assign(window.__ORCH_INDEX_URL__);
        });
      });
    }

    if (nameReviewForm) {
      nameReviewForm.addEventListener("submit", function (event) {
        var token;
        var fileInput;
        var hasFile;
        event.preventDefault();
        if (!currentJobId || isButtonBusy(continueNameReviewButton)) {
          return;
        }
        fileInput = nameReviewForm.querySelector('input[name="names_csv"]');
        hasFile = Boolean(fileInput && fileInput.files && fileInput.files.length);
        token = beginAction(
          continueNameReviewButton,
          hasFile ? "Uploading reviewed names" : "Accepting reviewed names",
          "Preparing the authoritative Name/Page roster...",
          "Continuing..."
        );
        postFormWithProgress(
          nameReviewForm,
          continueNameReviewUrl(currentJobId),
          function (percent, loaded, total) {
            if (percent == null) {
              showActionFeedback("Applying reviewed names", "Clearing stale downstream artifacts and starting metadata...", null);
            } else if (hasFile) {
              showActionFeedback("Uploading reviewed names", formatBytes(loaded) + " of " + formatBytes(total), percent);
            }
          },
          function (error, payload, response) {
            if (response && response.status === 409 && payload) {
              finishAction(continueNameReviewButton, token, "Review already continuing", "Switched to the active job.", false);
              activateJob(payload);
              return;
            }
            if (error || !response || !response.ok || !payload) {
              failAction(continueNameReviewButton, "Name review request failed", "The reviewed roster was not continued.");
              renderNameReview(currentPayload);
              showClientError("Name review continue request failed.", error || new Error("Name review continue request failed."));
              return;
            }
            clearClientError();
            finishAction(continueNameReviewButton, token, "Reviewed roster accepted", "Metadata and places are starting now.", false);
            activateJob(payload);
          }
        );
      });
    }

    render(currentPayload);
    refreshJobs();
    if (currentJobId) {
      refreshOutputs(currentJobId);
      refresh(currentJobId, function () {
        if (isActiveStatus(currentPayload.status)) {
          connect(currentJobId);
        }
      });
    } else {
      refreshOutputs(currentJobId);
      showClientStatus("No live job selected.");
    }
    window.addEventListener("pagehide", function () {
      stopReconnect();
      closeSource();
      stopPolling();
      if (refreshHandle) {
        window.clearTimeout(refreshHandle);
        refreshHandle = null;
      }
    });
  } catch (error) {
    showClientError("Dashboard refresh script failed to start. Server-rendered status is still shown below.", error);
  }
})();
