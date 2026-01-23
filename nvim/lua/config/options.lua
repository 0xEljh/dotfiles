-- Options are automatically loaded before lazy.nvim startup
-- Default options that are always set: https://github.com/LazyVim/LazyVim/blob/main/lua/lazyvim/config/options.lua
-- Add any additional options here

-- Clipboard configuration
-- Detect environment and configure clipboard accordingly

vim.g.clipboard = {
  name = "OSC 52 (copy only)",
  copy = {
    ["+"] = require("vim.ui.clipboard.osc52").copy("+"),
    ["*"] = require("vim.ui.clipboard.osc52").copy("*"),
  },
  paste = {
    -- Use terminal paste instead to avoid paste sync issues
    ["+"] = function()
      return {}
    end,
    ["*"] = function()
      return {}
    end,
  },
}

-- always sync with system clipboard
vim.opt.clipboard = "unnamedplus"
