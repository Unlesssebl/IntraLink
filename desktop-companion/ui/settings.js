const invoke = window.__TAURI__.core.invoke;
const ids = ["core_api_url", "litemanager_path", "dameware_path", "rdp_path"];
const status = document.getElementById("status");

invoke("get_settings")
  .then((settings) => {
    ids.forEach((id) => {
      document.getElementById(id).value = settings[id] || "";
    });
  })
  .catch((error) => {
    status.textContent = String(error);
  });

document.getElementById("save").addEventListener("click", async () => {
  try {
    const settings = Object.fromEntries(
      ids.map((id) => [id, document.getElementById(id).value.trim()]),
    );
    await invoke("save_settings", { settings });
    status.textContent = "Сохранено";
  } catch (error) {
    status.textContent = String(error);
  }
});
