const initial = {
  backlog: ["browser-tests", "release-notes"],
  progress: ["tag-release"],
  done: ["polish-demo"],
};

const cardById = new Map(
  [...document.querySelectorAll(".card")].map((card) => [card.dataset.card, card]),
);

let dragged = null;

function state() {
  return {
    columns: Object.fromEntries(
      [...document.querySelectorAll(".dropzone")].map((zone) => [
        zone.dataset.column,
        [...zone.querySelectorAll(".card")].map((card) => card.dataset.card),
      ]),
    ),
  };
}

function sync() {
  for (const zone of document.querySelectorAll(".dropzone")) {
    document.querySelector(`[data-count="${zone.dataset.column}"]`).textContent =
      zone.querySelectorAll(".card").length;
  }
  localStorage.setItem("qwen-cua-kanban", JSON.stringify(state()));
  document.querySelector("#status").textContent =
    "Board saved · " + new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function render(columns) {
  for (const [column, cards] of Object.entries(columns)) {
    const zone = document.querySelector(`[data-column="${column}"]`);
    for (const card of cards) {
      zone.appendChild(cardById.get(card));
    }
  }
  sync();
}

for (const card of document.querySelectorAll(".card")) {
  card.addEventListener("dragstart", () => {
    dragged = card;
    card.classList.add("is-dragging");
  });
  card.addEventListener("dragend", () => {
    card.classList.remove("is-dragging");
    dragged = null;
    sync();
  });
}

for (const zone of document.querySelectorAll(".dropzone")) {
  zone.addEventListener("dragover", (event) => {
    event.preventDefault();
    zone.classList.add("is-over");
    const candidates = [...zone.querySelectorAll(".card:not(.is-dragging)")];
    const next = candidates.find((card) => {
      const rect = card.getBoundingClientRect();
      return event.clientY < rect.top + rect.height / 2;
    });
    if (dragged) {
      zone.insertBefore(dragged, next || null);
    }
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("is-over"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("is-over");
    sync();
  });
}

document.querySelector("#reset").addEventListener("click", () => {
  render(initial);
  document.querySelector("#status").textContent = "Board reset.";
});

const saved = JSON.parse(localStorage.getItem("qwen-cua-kanban") || "null");
render(saved?.columns || initial);
window.__QWEN_CUA_STATE__ = state;

