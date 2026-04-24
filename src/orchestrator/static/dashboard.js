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
      .replace(/>/g, "&gt;");
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
        if (!response.ok) {
          callback(null, null, response);
          return;
        }
        response.json().then(function (payload) {
          callback(null, payload, response);
        }).catch(function (error) {
          callback(error);
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
      if (xhr.status < 200 || xhr.status >= 300) {
        callback(null, null, { ok: false, status: xhr.status });
        return;
      }
      try {
        callback(null, JSON.parse(xhr.responseText), { ok: true, status: xhr.status });
      } catch (error) {
        callback(error);
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
    var jobList = byId("job-list");
    var uploadForm = byId("upload-form");
    var existingForm = byId("existing-form");
    var resumeButton = byId("resume-job");
    var pauseButton = byId("pause-job");
    var cancelButton = byId("cancel-job");
    var clearResultsButton = byId("clear-results");
    var currentJobId = initialJobId;
    var currentPayload = initialJob || {};
    var source = null;
    var pollHandle = null;
    var reconnectHandle = null;

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
      metrics = objectEntries(stats).slice(0, 8);
      cleanup = Array.isArray(data.cleanup_actions) ? data.cleanup_actions.slice(0, 6) : [];

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
      var active = includes(["running", "cancelling", "pausing"], status);
      var canPause = status === "running";
      var canCancel = includes(["running", "pausing"], status);
      var docId = payload.doc_id || "";
      var jobId = payload.job_id || "";

      if (resumeButton) {
        resumeButton.dataset.docId = docId;
        resumeButton.disabled = !docId || active;
      }
      if (pauseButton) {
        pauseButton.dataset.jobId = jobId;
        pauseButton.disabled = !jobId || !canPause;
      }
      if (cancelButton) {
        cancelButton.dataset.jobId = jobId;
        cancelButton.disabled = !jobId || !canCancel;
      }
      if (clearResultsButton) {
        clearResultsButton.dataset.docId = docId;
        clearResultsButton.disabled = !docId || active;
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
      syncControls(payload);
      syncPolling();
      syncHistory(currentJobId);
    }

    function refreshJobs(done) {
      if (!jobList) {
        if (done) {
          done();
        }
        return;
      }
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
        clearClientError();
        render(payload);
        refreshJobs(function () {
          refreshOutputs(payload.job_id, done);
        });
      });
    }

    function stopReconnect() {
      if (reconnectHandle) {
        window.clearTimeout(reconnectHandle);
        reconnectHandle = null;
      }
    }

    function syncPolling() {
      var status = currentPayload.status || "";
      var shouldPoll = Boolean(currentJobId) && includes(["pending", "running", "pausing", "cancelling"], status);
      if (!shouldPoll) {
        if (pollHandle) {
          window.clearInterval(pollHandle);
          pollHandle = null;
        }
        return;
      }
      if (!pollHandle) {
        pollHandle = window.setInterval(function () {
          if (currentJobId) {
            refresh(currentJobId);
          }
        }, 3000);
      }
    }

    function scheduleReconnect(jobId) {
      stopReconnect();
      reconnectHandle = window.setTimeout(function () {
        if (currentJobId === jobId) {
          connect(jobId);
        }
      }, 2000);
    }

    function connect(jobId) {
      if (!jobId) {
        showClientStatus("No live job selected.");
        return;
      }
      if (source) {
        source.close();
        source = null;
      }
      if (!window.EventSource) {
        showClientStatus("Using polling fallback for live updates.");
        syncPolling();
        return;
      }
      source = new EventSource(streamUrl(jobId));
      source.onopen = function () {
        clearClientError();
        showClientStatus("Live updates connected.");
      };
      source.addEventListener("snapshot", function () { refresh(jobId); });
      source.addEventListener("status", function () { refresh(jobId); });
      source.addEventListener("page_updated", function () { refresh(jobId); });
      source.addEventListener("log", function () { refresh(jobId); });
      source.addEventListener("pause_requested", function () { refresh(jobId); });
      source.addEventListener("cancel_requested", function () { refresh(jobId); });
      source.addEventListener("done", function () { refresh(jobId); });
      source.onerror = function () {
        showClientStatus("Live stream disconnected. Retrying...");
        scheduleReconnect(jobId);
      };
    }

    function postForm(form) {
      var body = new FormData(form);
      requestJson(window.__ORCH_RUN_URL__, { method: "POST", body: body }, function (error, payload, response) {
        if (error || !response || !response.ok || !payload) {
          showClientError("Run request failed.", error || new Error("Run request failed."));
          return;
        }
        window.location.assign(window.__ORCH_INDEX_URL__ + "?job_id=" + encodeURIComponent(payload.job_id));
      });
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
        postForm(uploadForm);
      });
    }

    if (existingForm) {
      existingForm.addEventListener("submit", function (event) {
        event.preventDefault();
        postForm(existingForm);
      });
    }

    if (resumeButton) {
      resumeButton.addEventListener("click", function () {
        if (!resumeButton.dataset.docId) {
          return;
        }
        postEmpty(resumeUrl(resumeButton.dataset.docId), function (error, payload, response) {
          if (error || !response || !response.ok || !payload) {
            showClientError("Resume request failed.", error || new Error("Resume request failed."));
            return;
          }
          window.location.assign(window.__ORCH_INDEX_URL__ + "?job_id=" + encodeURIComponent(payload.job_id));
        });
      });
    }

    if (pauseButton) {
      pauseButton.addEventListener("click", function () {
        if (!pauseButton.dataset.jobId) {
          return;
        }
        postEmpty(pauseUrl(pauseButton.dataset.jobId), function () {
          refresh(pauseButton.dataset.jobId);
        });
      });
    }

    if (cancelButton) {
      cancelButton.addEventListener("click", function () {
        if (!cancelButton.dataset.jobId) {
          return;
        }
        postEmpty(cancelUrl(cancelButton.dataset.jobId), function () {
          refresh(cancelButton.dataset.jobId);
        });
      });
    }

    if (clearResultsButton) {
      clearResultsButton.addEventListener("click", function () {
        var confirmed;
        if (!clearResultsButton.dataset.docId) {
          return;
        }
        confirmed = window.confirm("Clear all generated results for " + clearResultsButton.dataset.docId + "? This keeps the source PDF but removes pages, OCR text, intermediate JSON, outputs, logs, and audit artifacts.");
        if (!confirmed) {
          return;
        }
        postEmpty(clearResultsUrl(clearResultsButton.dataset.docId), function (error, payload, response) {
          if (error || !response || !response.ok) {
            showClientError("Clear results request failed.", error || new Error("Clear results request failed."));
            return;
          }
          window.location.assign(window.__ORCH_INDEX_URL__);
        });
      });
    }

    render(currentPayload);
    refreshJobs();
    if (currentJobId) {
      refresh(currentJobId, function () {
        connect(currentJobId);
      });
    } else {
      refreshOutputs(currentJobId);
      showClientStatus("No live job selected.");
    }
  } catch (error) {
    showClientError("Dashboard refresh script failed to start. Server-rendered status is still shown below.", error);
  }
})();
