-- Options are automatically loaded before lazy.nvim startup
-- Default options that are always set: https://github.com/LazyVim/LazyVim/blob/main/lua/lazyvim/config/options.lua
-- Add any additional options here

-- Clipboard configuration
-- Detect environment and configure clipboard accordingly

local function is_wsl()
  local output = vim.fn.systemlist("uname -r")
  return output[1] and output[1]:lower():match("microsoft") ~= nil
end

if is_wsl() then
  -- WSL: Use Windows clipboard integration
  vim.g.clipboard = {
    name = "wsl-windows-clipboard",
    copy = {
      ["+"] = "clip.exe",
      ["*"] = "clip.exe",
    },
    paste = {
      ["+"] = "powershell.exe -NoProfile -Command Get-Clipboard",
      ["*"] = "powershell.exe -NoProfile -Command Get-Clipboard",
    },
    cache_enabled = 0,
  }
  vim.opt.clipboard = "unnamedplus"
elseif os.getenv("SSH_CONNECTION") then
  -- SSH: Use system clipboard
  vim.opt.clipboard = "unnamedplus"
end
