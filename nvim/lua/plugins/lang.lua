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
        pyright = {},
        ruff_lsp = {},
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
