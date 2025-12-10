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
  -- WSL: Use win32yank for clipboard integration with Windows
  vim.g.clipboard = {
    name = "win32yank-wsl",
    copy = {
      ["+"] = "win32yank.exe -i --crlf",
      ["*"] = "win32yank.exe -i --crlf",
    },
    paste = {
      ["+"] = "win32yank.exe -o --lf",
      ["*"] = "win32yank.exe -o --lf",
    },
    cache_enabled = 0,
  }
  vim.opt.clipboard = "unnamedplus"
elseif os.getenv("SSH_CONNECTION") then
  -- SSH: Use system clipboard
  vim.opt.clipboard = "unnamedplus"
end
