/* Slideshow display logic */
(function () {
  "use strict";

  const photoA = document.getElementById("photo-a");
  const photoB = document.getElementById("photo-b");
  const bgLayer = document.getElementById("bg-layer");
  const infoOverlay = document.getElementById("info-overlay");
  const infoText = document.getElementById("info-text");
  const powerOverlay = document.getElementById("power-save-overlay");

  let activeImg = photoA;
  let nextImg = photoB;
  let display = {};
  let interval = 30;
  let paused = false;
  let timer = null;
  let currentPhotoUrl = null;

  /* ---- API helpers ---- */

  async function fetchJSON(url, opts) {
    const res = await fetch(url, opts);
    return res.json();
  }

  /* ---- Display ---- */

  function applyFitClass(img) {
    img.classList.remove("fit-fit", "fit-fill", "fit-ken_burns", "ken-burns-active");
    const mode = display.fit_mode || "fit";
    img.classList.add("fit-" + mode);
    if (mode === "ken_burns") {
      // restart animation
      void img.offsetWidth;
      img.classList.add("ken-burns-active");
    }
  }

  function setTransitionDuration() {
    const dur = (display.transition_duration || 1.5) + "s";
    photoA.style.transitionDuration = dur;
    photoB.style.transitionDuration = dur;
  }

  function showPhoto(url, photo) {
    if (!url) return;
    currentPhotoUrl = url;

    // Preload image
    const preload = new Image();
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
      const transition = display.transition || "fade";
      if (transition === "slide") {
        doSlideTransition();
      } else {
        doFadeTransition();
      }

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
    const tmp = activeImg;
    activeImg = nextImg;
    nextImg = tmp;
  }

  function doSlideTransition() {
    nextImg.classList.remove("slide-enter", "slide-in", "slide-out");
    activeImg.classList.remove("slide-enter", "slide-in", "slide-out");
    nextImg.classList.add("slide-enter");
    void nextImg.offsetWidth; // force reflow
    nextImg.classList.add("slide-in");
    nextImg.classList.remove("slide-enter");
    activeImg.classList.add("slide-out");
    activeImg.classList.remove("active");
    nextImg.classList.add("active");
    const tmp = activeImg;
    activeImg = nextImg;
    nextImg = tmp;
    // clean up old
    setTimeout(function () {
      tmp.classList.remove("slide-out", "slide-in", "active");
    }, (display.transition_duration || 1.5) * 1000 + 100);
  }

  function showInfo(photo) {
    const parts = [];
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

  async function advance() {
    const data = await fetchJSON("/api/control/next", { method: "POST" });
    if (data.photo) showPhoto(data.photo.url, data.photo);
  }

  function startTimer() {
    stopTimer();
    timer = setInterval(function () {
      if (!paused) advance();
    }, interval * 1000);
  }

  function stopTimer() {
    if (timer) clearInterval(timer);
    timer = null;
  }

  /* ---- Schedule check ---- */

  async function checkSchedule() {
    const sched = await fetchJSON("/api/schedule");
    // Power save
    if (sched.power_save) {
      powerOverlay.classList.remove("hidden");
    } else {
      powerOverlay.classList.add("hidden");
    }
    // Night mode brightness
    if (sched.night_mode && !sched.power_save) {
      document.body.style.filter = "brightness(" + sched.night_brightness + ")";
    } else if (!sched.power_save) {
      document.body.style.filter = "";
    }
  }

  /* ---- Init ---- */

  async function init() {
    const data = await fetchJSON("/api/now-playing");
    display = data.display || {};
    interval = data.interval || 30;
    paused = data.paused;

    setTransitionDuration();

    if (data.photo) {
      showPhoto(data.photo.url, data.photo);
    }

    startTimer();
    // Check schedule every 60 seconds
    checkSchedule();
    setInterval(checkSchedule, 60000);
  }

  init();
})();
