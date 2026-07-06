local function native_library_path(plugin)
  local filename = "libfff_nvim.so"

  if jit.os == "OSX" then
    filename = "libfff_nvim.dylib"
  elseif jit.os == "Windows" then
    filename = "fff_nvim.dll"
  end

  return plugin.dir .. "/target/release/" .. filename
end

local function build_native_backend(plugin)
  local result = vim.system({ "nix", "run", ".#release" }, {
    cwd = plugin.dir,
    text = true,
  }):wait()

  if result.code ~= 0 then
    error("Failed to build fff.nvim native backend:\n" .. (result.stdout or "") .. (result.stderr or ""))
  end

  local library = native_library_path(plugin)
  if not vim.uv.fs_stat(library) then
    error("fff.nvim native backend build finished, but did not create " .. library)
  end
end

return {
  {
    "dmtrKovalenko/fff.nvim",
    build = build_native_backend,
    init = function(plugin)
      if vim.uv.fs_stat(native_library_path(plugin)) then
        return
      end

      vim.schedule(function()
        vim.notify(
          "fff.nvim native backend is missing; run :Lazy build fff.nvim before using FFF pickers",
          vim.log.levels.WARN
        )
      end)
    end,
    lazy = false,
    opts = {
      lazy_sync = true,
    },
  },
}
