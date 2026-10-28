vim.g.lazyvim_python_lsp = "ty"

local function paste()
  return {
    vim.fn.split(vim.fn.getreg(""), "\n"),
    vim.fn.getregtype(""),
  }
end

local function copy(reg)
  local clipboard = reg == "+" and "c" or "p"

  return function(lines)
    local sequence = string.format("\027]52;%s;%s\027\\", clipboard, vim.base64.encode(table.concat(lines, "\n")))

    if vim.env.TMUX then
      sequence = "\027Ptmux;" .. sequence:gsub("\027", "\027\027") .. "\027\\"
    end

    vim.api.nvim_ui_send(sequence)
  end
end

vim.g.clipboard = {
  name = "OSC 52",
  copy = {
    ["+"] = copy("+"),
    ["*"] = copy("*"),
  },
  paste = {
    ["+"] = paste,
    ["*"] = paste,
  },
}

vim.opt.clipboard = "unnamedplus"
