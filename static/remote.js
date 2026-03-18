/* Remote control logic */
(function () {
  "use strict";

  const playlistSelect = document.getElementById("playlist-select");
  const currentPhotoImg = document.getElementById("current-photo");
  const ratingDisplay = document.getElementById("rating-display");
  const peopleDisplay = document.getElementById("people-display");
  const keywordsDisplay = document.getElementById("keywords-display");
  const btnPrev = document.getElementById("btn-prev");
  const btnPause = document.getElementById("btn-pause");
  const btnNext = document.getElementById("btn-next");
  const ratingEditor = document.getElementById("rating-editor");
  const stars = document.querySelectorAll(".star");
  const btnClearRating = document.getElementById("btn-clear-rating");
  const historyStrip = document.getElementById("history-strip");

  let currentPhoto = null;
  let isPaused = false;
  let pollTimer = null;

  /* ---- API helpers ---- */

  async function api(url, opts) {
    const res = await fetch(url, opts);
    return res.json();
  }

  function post(url) {
    return api(url, { method: "POST" });
  }

  /* ---- Update display ---- */

  function updateNowPlaying(photo) {
    if (!photo) return;
    currentPhoto = photo;
    currentPhotoImg.src = photo.url;

    if (photo.rating) {
      ratingDisplay.textContent = "\u2605".repeat(photo.rating) + "\u2606".repeat(5 - photo.rating);
    } else {
      ratingDisplay.textContent = "\u2606".repeat(5);
    }

    peopleDisplay.textContent = (photo.people || []).join(", ");
    keywordsDisplay.textContent = (photo.keywords || []).join(", ");

    updateStars(photo.rating || 0);
    ratingEditor.classList.remove("hidden");
  }

  function updateStars(rating) {
    stars.forEach(function (s) {
      s.classList.toggle("lit", parseInt(s.dataset.rating) <= rating);
    });
  }

  function updatePauseButton() {
    btnPause.innerHTML = isPaused ? "&#9654;" : "&#9646;&#9646;";
  }

  /* ---- Playlists ---- */

  async function loadPlaylists() {
    const playlists = await api("/api/playlists");
    playlistSelect.innerHTML = "";
    playlists.forEach(function (pl) {
      const opt = document.createElement("option");
      opt.value = pl.id;
      opt.textContent = pl.name;
      if (pl.active) opt.selected = true;
      playlistSelect.appendChild(opt);
    });
  }

  playlistSelect.addEventListener("change", async function () {
    const data = await post("/api/control/playlist/" + playlistSelect.value);
    if (data.photo) updateNowPlaying(data.photo);
  });

  /* ---- Controls ---- */

  btnNext.addEventListener("click", async function () {
    const data = await post("/api/control/next");
    if (data.photo) updateNowPlaying(data.photo);
  });

  btnPrev.addEventListener("click", async function () {
    const data = await post("/api/control/prev");
    if (data.photo) updateNowPlaying(data.photo);
  });

  btnPause.addEventListener("click", async function () {
    const data = await post("/api/control/pause");
    isPaused = data.paused;
    updatePauseButton();
  });

  /* ---- Rating ---- */

  stars.forEach(function (s) {
    s.addEventListener("click", function () {
      if (!currentPhoto) return;
      const rating = parseInt(s.dataset.rating);
      setRating(rating);
    });
  });

  btnClearRating.addEventListener("click", function () {
    if (currentPhoto) setRating(0);
  });

  async function setRating(rating) {
    const val = rating === 0 ? null : rating;
    const photoId = currentPhoto.id || currentPhoto.photo_id;
    await api("/api/photos/" + photoId + "/rating", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rating: val }),
    });
    currentPhoto.rating = val;
    updateStars(rating);
    if (val) {
      ratingDisplay.textContent = "\u2605".repeat(val) + "\u2606".repeat(5 - val);
    } else {
      ratingDisplay.textContent = "\u2606".repeat(5);
    }
  }

  /* ---- Tap photo to open full-size ---- */

  currentPhotoImg.addEventListener("click", function () {
    if (currentPhoto) window.open(currentPhoto.url, "_blank");
  });

  /* ---- History ---- */

  async function loadHistory() {
    const items = await api("/api/history?limit=20");
    historyStrip.innerHTML = "";
    items.forEach(function (item) {
      const img = document.createElement("img");
      img.src = item.url;
      img.alt = "";
      img.addEventListener("click", function () {
        window.open(item.url, "_blank");
      });
      historyStrip.appendChild(img);
    });
  }

  /* ---- Polling ---- */

  async function poll() {
    const data = await api("/api/now-playing");
    isPaused = data.paused;
    updatePauseButton();
    if (data.photo && (!currentPhoto || data.photo.url !== currentPhoto.url)) {
      updateNowPlaying(data.photo);
      loadHistory();
    }
  }

  /* ---- Init ---- */

  async function init() {
    await loadPlaylists();
    await poll();
    await loadHistory();
    pollTimer = setInterval(poll, 3000);
  }

  init();
})();
