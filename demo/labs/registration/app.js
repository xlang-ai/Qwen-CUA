const form = document.querySelector("#registration-form");
let submittedState = null;

function currentState() {
  const data = new FormData(form);
  return {
    name: String(data.get("name") || ""),
    email: String(data.get("email") || ""),
    ticket: String(data.get("ticket") || ""),
    workshop: String(data.get("workshop") || ""),
    meal: String(data.get("meal") || ""),
    conduct: data.get("conduct") === "on",
    submitted: Boolean(submittedState),
  };
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const state = currentState();
  if (
    !state.name ||
    !state.email ||
    !state.ticket ||
    !state.workshop ||
    !state.conduct
  ) {
    document.querySelector("#error").textContent =
      "Please complete every required field before submitting.";
    return;
  }
  submittedState = { ...state, submitted: true };
  document.querySelector("#form-view").hidden = true;
  document.querySelector("#confirmation").hidden = false;
  document.querySelector("#confirmation-name").textContent = state.name;
  document.querySelector("#confirmation-copy").textContent =
    `A confirmation for the ${state.ticket} ticket was prepared for ${state.email}.`;
});

window.__QWEN_CUA_STATE__ = () => submittedState || currentState();

