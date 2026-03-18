/* Slideshow display logic */
(function () {
  "use strict";

  var photoA = document.getElementById("photo-a");
  var photoB = document.getElementById("photo-b");
  var bgLayer = document.getElementById("bg-layer");
  var infoOverlay = document.getElementById("info-overlay");
  var infoText = document.getElementById("info-text");
  var powerOverlay = document.getElementById("power-save-overlay");
  var controlsOverlay = document.getElementById("controls-overlay");
  var controlsTrigger = document.getElementById("controls-trigger");
  var slPlaylist = document.getElementById("sl-playlist");
  var slPrev = document.getElementById("sl-prev");
  var slPause = document.getElementById("sl-pause");
  var slNext = document.getElementById("sl-next");

  var activeImg = photoA;
  var nextImg = photoB;
  var display = {};
  var interval = 30;
  var paused = false;
  var advanceTimer = null;
  var currentPhotoUrl = null;
  var controlsHideTimer = null;

  /* ---- API helpers ---- */

  function fetchJSON(url, opts) {
    return fetch(url, opts).then(function (r) { return r.json(); });
  }
  function post(url) { return fetchJSON(url, { method: "POST" }); }

  /* ---- Controls overlay ---- */

  function showControls() {
    controlsOverlay.classList.remove("hidden");
    document.body.classList.add("controls-visible");
    clearTimeout(controlsHideTimer);
    controlsHideTimer = setTimeout(hideControls, 3000);
  }

  function hideControls() {
    controlsOverlay.classList.add("hidden");
    document.body.classList.remove("controls-visible");
  }

  document.addEventListener("mousemove", showControls);
  controlsTrigger.addEventListener("click", showControls);

  controlsOverlay.addEventListener("mouseenter", function () {
    clearTimeout(controlsHideTimer);
  });
  controlsOverlay.addEventListener("mouseleave", function () {
    controlsHideTimer = setTimeout(hideControls, 2000);
  });

  slNext.addEventListener("click", function () {
    post("/api/control/next").then(function (d) {
      if (d.photo) showPhoto(d.photo.url, d.photo);
    });
  });

  slPrev.addEventListener("click", function () {
    post("/api/control/prev").then(function (d) {
      if (d.photo) showPhoto(d.photo.url, d.photo);
    });
  });

  slPause.addEventListener("click", function () {
    post("/api/control/pause").then(function (d) {
      paused = d.paused;
      updatePauseButton();
    });
  });

  slPlaylist.addEventListener("change", function () {
    post("/api/control/playlist/" + slPlaylist.value).then(function (d) {
      if (d.photo) showPhoto(d.photo.url, d.photo);
      // Reload display settings for new playlist
      fetchJSON("/api/now-playing").then(applyState);
    });
  });

  function updatePauseButton() {
    slPause.innerHTML = paused ? "&#9654;" : "&#9646;&#9646;";
  }

  function loadPlaylists() {
    fetchJSON("/api/playlists").then(function (playlists) {
      slPlaylist.innerHTML = "";
      playlists.forEach(function (pl) {
        var opt = document.createElement("option");
        opt.value = pl.id;
        opt.textContent = pl.name;
        if (pl.active) opt.selected = true;
        slPlaylist.appendChild(opt);
      });
    });
  }

  /* ---- Display ---- */

  function applyFitClass(img) {
    img.classList.remove("fit-fit", "fit-fill", "fit-ken_burns", "ken-burns-active");
    var mode = display.fit_mode || "fit";
    img.classList.add("fit-" + mode);
    // Ken Burns is started separately after the image becomes visible
  }

  function startKenBurns(img) {
    if ((display.fit_mode || "fit") !== "ken_burns") return;
    img.classList.remove("ken-burns-active");
    // Randomize direction per photo
    var scale = 1.2 + Math.random() * 0.15;
    var x = (Math.random() - 0.5) * 8;
    var y = (Math.random() - 0.5) * 6;
    img.style.setProperty("--kb-scale", scale);
    img.style.setProperty("--kb-x", x + "%");
    img.style.setProperty("--kb-y", y + "%");
    img.style.setProperty("--kb-duration", interval + "s");
    // Double rAF ensures the browser has painted the visible element
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        img.classList.add("ken-burns-active");
      });
    });
  }

  function setTransitionDuration() {
    var dur = (display.transition_duration || 1.5) + "s";
    photoA.style.transitionDuration = dur;
    photoB.style.transitionDuration = dur;
  }

  function showPhoto(url, photo) {
    if (!url) return;
    currentPhotoUrl = url;

    var preload = new Image();
    preload.onload = function () {
      nextImg.src = url;
      applyFitClass(nextImg);

      // Background
      if (display.background === "blur") {
        bgLayer.style.backgroundImage = 'url("' + url + '")';
        bgLayer.classList.add("visible");
      } else {
        bgLayer.classList.remove("visible");
      }

      // Transition
      var transition = display.transition || "fade";
      if (transition === "slide") {
        doSlideTransition();
      } else {
        doFadeTransition();
      }

      // Start Ken Burns after the image is visible
      startKenBurns(activeImg);

      // Info overlay
      if (display.show_info_overlay && photo) {
        showInfo(photo);
      } else {
        infoOverlay.classList.add("hidden");
      }
    };
    preload.src = url;
  }

  function doFadeTransition() {
    activeImg.classList.remove("active");
    nextImg.classList.add("active");
    var tmp = activeImg;
    activeImg = nextImg;
    nextImg = tmp;
  }

  function doSlideTransition() {
    nextImg.classList.remove("slide-enter", "slide-in", "slide-out");
    activeImg.classList.remove("slide-enter", "slide-in", "slide-out");
    nextImg.classList.add("slide-enter");
    void nextImg.offsetWidth;
    nextImg.classList.add("slide-in");
    nextImg.classList.remove("slide-enter");
    activeImg.classList.add("slide-out");
    activeImg.classList.remove("active");
    nextImg.classList.add("active");
    var tmp = activeImg;
    activeImg = nextImg;
    nextImg = tmp;
    setTimeout(function () {
      tmp.classList.remove("slide-out", "slide-in", "active");
    }, (display.transition_duration || 1.5) * 1000 + 100);
  }

  function showInfo(photo) {
    var parts = [];
    if (photo.relative_path) parts.push(photo.relative_path);
    if (photo.rating) parts.push("\u2605".repeat(photo.rating));
    if (photo.people && photo.people.length) parts.push(photo.people.join(", "));
    if (photo.keywords && photo.keywords.length) parts.push(photo.keywords.join(", "));
    if (parts.length) {
      infoText.textContent = parts.join("  \u00b7  ");
      infoOverlay.classList.remove("hidden");
    } else {
      infoOverlay.classList.add("hidden");
    }
  }

  /* ---- Slideshow loop ---- */

  function advance() {
    post("/api/control/next").then(function (data) {
      if (data.photo) showPhoto(data.photo.url, data.photo);
    });
  }

  function startTimer() {
    stopTimer();
    advanceTimer = setInterval(function () {
      if (!paused) advance();
    }, interval * 1000);
  }

  function stopTimer() {
    if (advanceTimer) clearInterval(advanceTimer);
    advanceTimer = null;
  }

  /* ---- State polling — picks up pause/skip/playlist changes from remote ---- */

  function applyState(data) {
    var oldPaused = paused;
    var oldInterval = interval;
    paused = data.paused;
    display = data.display || {};
    interval = data.interval || 30;

    if (paused !== oldPaused) updatePauseButton();
    if (interval !== oldInterval) {
      setTransitionDuration();
      startTimer();
    }

    if (data.photo && data.photo.url !== currentPhotoUrl) {
      showPhoto(data.photo.url, data.photo);
    }
  }

  function pollState() {
    fetchJSON("/api/now-playing").then(applyState);
  }

  /* ---- Schedule check ---- */

  function checkSchedule() {
    fetchJSON("/api/schedule").then(function (sched) {
      if (sched.power_save) {
        powerOverlay.classList.remove("hidden");
      } else {
        powerOverlay.classList.add("hidden");
      }
      if (sched.night_mode && !sched.power_save) {
        document.body.style.filter = "brightness(" + sched.night_brightness + ")";
      } else if (!sched.power_save) {
        document.body.style.filter = "";
      }
    });
  }

  /* ---- Init ---- */

  function init() {
    loadPlaylists();
    fetchJSON("/api/now-playing").then(function (data) {
      applyState(data);
      setTransitionDuration();
      if (data.photo) showPhoto(data.photo.url, data.photo);
      startTimer();
      // Poll for remote-initiated changes every 3 seconds
      setInterval(pollState, 3000);
      // Check schedule every 60 seconds
      checkSchedule();
      setInterval(checkSchedule, 60000);
    });
  }

  init();
})();
