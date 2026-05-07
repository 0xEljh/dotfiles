local python_root_markers = {
  "pyproject.toml",
  "uv.lock",
  "setup.py",
  "setup.cfg",
  "requirements.txt",
  "Pipfile",
  "pyrightconfig.json",
  ".git",
}

local python_env_names = { ".venv", "venv" }
local is_windows = vim.fn.has("win32") == 1 or vim.fn.has("win64") == 1
local python_bin_dir = is_windows and "Scripts" or "bin"
local python_bin_name = is_windows and "python.exe" or "python"

local function python_root_dir(path)
  -- Neovim 0.11's vim.lsp.enable() passes bufnr (number) instead of filename
  if type(path) == "number" then
    path = vim.api.nvim_buf_get_name(path)
  end

  if not path or path == "" then
    return nil
  end

  local dir = vim.fn.isdirectory(path) == 1 and path or vim.fs.dirname(path)
  if not dir or dir == "" then
    return nil
  end

  return vim.fs.root(dir, python_root_markers) or dir
end

local function local_python(root_dir)
  if not root_dir or root_dir == "" then
    return nil
  end

  for _, env_name in ipairs(python_env_names) do
    local python = vim.fs.joinpath(root_dir, env_name, python_bin_dir, python_bin_name)
    if vim.fn.executable(python) == 1 then
      return python
    end
  end

  return nil
end

local function local_python_for_buf(bufnr)
  if not vim.api.nvim_buf_is_valid(bufnr) then
    return nil, nil
  end

  local bufname = vim.api.nvim_buf_get_name(bufnr)
  local root_dir = python_root_dir(bufname)
  return root_dir, local_python(root_dir)
end

local function apply_local_python_config(config, root_dir)
  local python = local_python(root_dir)
  if not python then
    return
  end

  local venv_root = vim.fs.dirname(vim.fs.dirname(python))
  config.settings = vim.tbl_deep_extend("force", config.settings or {}, {
    python = {
      pythonPath = python,
      venv = vim.fs.basename(venv_root),
      venvPath = vim.fs.dirname(venv_root),
    },
  })
  config.cmd_env = vim.tbl_deep_extend("force", config.cmd_env or {}, {
    VIRTUAL_ENV = venv_root,
  })
end

local function python_lsp_server_config()
  return {
    root_dir = function(fname)
      return python_root_dir(fname)
    end,
    settings = {
      python = {
        analysis = {
          autoImportCompletions = true,
          autoSearchPaths = true,
          diagnosticMode = "openFilesOnly",
          useLibraryCodeForTypes = true,
        },
      },
    },
    before_init = function(_, config)
      local root_dir = config.root_dir
      if type(root_dir) == "function" then
        root_dir = root_dir(vim.api.nvim_buf_get_name(0))
      end
      apply_local_python_config(config, root_dir)
    end,
    on_new_config = function(config, root_dir)
      apply_local_python_config(config, root_dir)
    end,
  }
end

local function auto_activate_local_python(bufnr)
  if not vim.api.nvim_buf_is_valid(bufnr) or vim.api.nvim_get_current_buf() ~= bufnr then
    return
  end

  if vim.bo[bufnr].buftype ~= "" or vim.bo[bufnr].filetype ~= "python" then
    return
  end

  if vim.b[bufnr].venv_selector_disabled or vim.b[bufnr].venv_selector_last_python then
    return
  end

  local ok_uv, uv2 = pcall(require, "venv-selector.uv2")
  if ok_uv and uv2.is_uv_buffer(bufnr) then
    return
  end

  local _, python = local_python_for_buf(bufnr)
  if not python then
    return
  end

  local ok_venv, venv = pcall(require, "venv-selector.venv")
  if not ok_venv then
    return
  end

  if require("venv-selector").python() == python then
    return
  end

  venv.set_source("local")
  venv.activate_for_buffer(python, "venv", bufnr, { save_cache = true })
end

return {
  -- Treesitter: ensure common parsers are installed
  {
    "nvim-treesitter/nvim-treesitter",
    opts = function(_, opts)
      vim.list_extend(opts.ensure_installed or {}, {
        "bash",
        "c",
        "cpp",
        "css",
        "dockerfile",
        "go",
        "html",
        "javascript",
        "json",
        "lua",
        "markdown",
        "markdown_inline",
        "python",
        "query",
        "regex",
        "rust",
        "sql",
        "toml",
        "tsx",
        "typescript",
        "vim",
        "vimdoc",
        "yaml",
      })
    end,
  },

  -- LSP: configure language servers
  {
    "neovim/nvim-lspconfig",
    opts = {
      servers = {
        -- Python
        pyright = python_lsp_server_config(),
        basedpyright = python_lsp_server_config(),
        ruff = {
          root_dir = function(fname)
            return python_root_dir(fname)
          end,
        },
        ruff_lsp = {
          root_dir = function(fname)
            return python_root_dir(fname)
          end,
        },
        -- TypeScript/JavaScript
        ts_ls = {},
        -- Go
        gopls = {},
        -- Rust
        rust_analyzer = {},
        -- Lua
        lua_ls = {},
        -- Bash
        bashls = {},
        -- Docker
        dockerls = {},
        -- YAML
        yamlls = {},
        -- JSON
        jsonls = {},
        -- CSS/HTML
        cssls = {},
        html = {},
        -- Tailwind
        tailwindcss = {},
      },
    },
  },

  {
    "linux-cultist/venv-selector.nvim",
    opts = function(_, opts)
      opts.options = vim.tbl_deep_extend("force", opts.options or {}, {
        notify_user_on_venv_activation = true,
      })
    end,
    config = function(_, opts)
      local venv_selector = require("venv-selector")
      venv_selector.setup(opts)

      local group = vim.api.nvim_create_augroup("DotfilesPythonLocalVenv", { clear = true })
      vim.api.nvim_create_autocmd({ "BufReadPost", "BufNewFile", "FileType", "BufEnter" }, {
        group = group,
        callback = function(args)
          local bufnr = args.buf
          vim.schedule(function()
            auto_activate_local_python(bufnr)
          end)
        end,
      })
    end,
  },

  -- Mason: ensure tools are installed
  {
    "mason-org/mason.nvim",
    opts = function(_, opts)
      vim.list_extend(opts.ensure_installed or {}, {
        -- Formatters
        "stylua",
        "prettier",
        "shfmt",
        "black",
        "isort",
        -- Linters
        "shellcheck",
        "eslint_d",
        "ruff",
        -- DAP
        "debugpy",
      })
    end,
  },
}
