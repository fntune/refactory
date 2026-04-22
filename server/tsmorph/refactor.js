#!/usr/bin/env node
/**
 * TypeScript refactoring operations using ts-morph.
 * Called by the Python backend via subprocess.
 */
const { Project } = require("ts-morph");
const { SyntaxKind } = require("ts-morph");
const path = require("path");
const fs = require("fs");

/**
 * Validate that a path stays within project root.
 * @param {string} filePath - The file path to validate
 * @param {string} projectRoot - The project root directory
 * @returns {string} - The resolved absolute path
 * @throws {Error} - If path escapes project root
 */
function validatePath(filePath, projectRoot) {
  const root = path.resolve(projectRoot);
  const resolved = path.resolve(root, filePath);
  if (!resolved.startsWith(root + path.sep) && resolved !== root) {
    throw new Error(`Path '${filePath}' escapes project root`);
  }
  return resolved;
}

/**
 * Get or create a ts-morph Project.
 */
function getProject(projectRoot) {
  const root = path.resolve(projectRoot);
  if (!fs.existsSync(root)) {
    throw new Error(`Project root does not exist: ${projectRoot}`);
  }

  const tsConfigPath = path.join(root, "tsconfig.json");
  if (fs.existsSync(tsConfigPath)) {
    return new Project({ tsConfigFilePath: tsConfigPath });
  }
  return new Project({
    compilerOptions: { allowJs: true, checkJs: false },
  });
}

function loadProjectSourceFiles(project, root) {
  project.addSourceFilesAtPaths([
    path.join(root, "**/*.ts"),
    path.join(root, "**/*.tsx"),
    path.join(root, "**/*.js"),
    path.join(root, "**/*.jsx"),
    "!" + path.join(root, "**/node_modules/**"),
  ]);

  return project
    .getSourceFiles()
    .filter((sourceFile) => {
      const filePath = sourceFile.getFilePath();
      return (
        filePath === root
        || filePath.startsWith(root + path.sep)
      ) && !filePath.includes(`${path.sep}node_modules${path.sep}`);
    });
}

function toModuleSpecifier(fromFilePath, targetFilePath) {
  let newSpecifier = path.relative(path.dirname(fromFilePath), targetFilePath);
  if (!newSpecifier.startsWith(".")) {
    newSpecifier = "./" + newSpecifier;
  }
  return newSpecifier.replace(/\\/g, "/").replace(/\.(ts|tsx|js|jsx)$/, "");
}

function resolveLocalModuleSpecifier(refFilePath, specifier) {
  if (!specifier.startsWith(".")) {
    return null;
  }

  const basePath = path.resolve(path.dirname(refFilePath), specifier);
  const candidates = [
    basePath,
    `${basePath}.ts`,
    `${basePath}.tsx`,
    `${basePath}.js`,
    `${basePath}.jsx`,
    path.join(basePath, "index.ts"),
    path.join(basePath, "index.tsx"),
    path.join(basePath, "index.js"),
    path.join(basePath, "index.jsx"),
  ];

  return candidates.find((candidate) => fs.existsSync(candidate)) || null;
}

function getRequireCallsReferencingFile(refFile, sourceFilePath) {
  return refFile.getDescendantsOfKind(SyntaxKind.CallExpression).filter((callExpr) => {
    if (callExpr.getExpression().getText() !== "require") {
      return false;
    }

    const [firstArg] = callExpr.getArguments();
    if (!firstArg) {
      return false;
    }

    const argKind = firstArg.getKind();
    if (argKind !== SyntaxKind.StringLiteral && argKind !== SyntaxKind.NoSubstitutionTemplateLiteral) {
      return false;
    }

    const resolvedPath = resolveLocalModuleSpecifier(refFile.getFilePath(), firstArg.getLiteralValue());
    return resolvedPath === sourceFilePath;
  });
}

function collectModuleReferences(files, sourceFilePath, predicate) {
  return files
    .filter((file) => file.getFilePath() !== sourceFilePath)
    .map((file) => predicate(file))
    .filter((entry) => (
      entry.imports.length > 0
      || entry.exports.length > 0
      || entry.requires.length > 0
    ));
}

function buildNamedSpecifier(name, alias, isTypeOnly) {
  if (!alias && !isTypeOnly) {
    return name;
  }

  return { name, alias, isTypeOnly };
}

function ensureNamedImport(refFile, moduleSpecifier, options) {
  const existingImport = refFile.getImportDeclarations().find((decl) => {
    return decl.getModuleSpecifierValue() === moduleSpecifier
      && !decl.getNamespaceImport()
      && !(decl.isTypeOnly() && !options.isTypeOnly);
  });

  if (existingImport) {
    const alreadyPresent = existingImport.getNamedImports().some((namedImport) => {
      const alias = namedImport.getAliasNode()?.getText();
      return namedImport.getName() === options.name
        && alias === options.alias
        && namedImport.isTypeOnly() === options.isTypeOnly;
    });

    if (!alreadyPresent) {
      existingImport.addNamedImport(
        buildNamedSpecifier(options.name, options.alias, options.isTypeOnly)
      );
    }
    return;
  }

  refFile.addImportDeclaration({
    moduleSpecifier,
    namedImports: [buildNamedSpecifier(options.name, options.alias, options.isTypeOnly)],
  });
}

function ensureNamedExport(refFile, moduleSpecifier, options) {
  const existingExport = refFile.getExportDeclarations().find((decl) => {
    return decl.getModuleSpecifierValue() === moduleSpecifier
      && !(decl.isTypeOnly() && !options.isTypeOnly);
  });

  if (existingExport) {
    const alreadyPresent = existingExport.getNamedExports().some((namedExport) => {
      const alias = namedExport.getAliasNode()?.getText();
      return namedExport.getName() === options.name
        && alias === options.alias
        && namedExport.isTypeOnly() === options.isTypeOnly;
    });

    if (!alreadyPresent) {
      existingExport.addNamedExport(
        buildNamedSpecifier(options.name, options.alias, options.isTypeOnly)
      );
    }
    return;
  }

  refFile.addExportDeclaration({
    moduleSpecifier,
    namedExports: [buildNamedSpecifier(options.name, options.alias, options.isTypeOnly)],
  });
}

function removeEmptyImportDeclaration(importDecl) {
  if (
    importDecl.getNamedImports().length === 0
    && !importDecl.getDefaultImport()
    && !importDecl.getNamespaceImport()
  ) {
    importDecl.remove();
  }
}

function removeEmptyExportDeclaration(exportDecl) {
  if (exportDecl.getNamedExports().length === 0) {
    exportDecl.remove();
  }
}

function getMovedSymbolText(symbol) {
  if (symbol.getKind() === SyntaxKind.VariableDeclaration) {
    const variableStatement = symbol.getVariableStatement();
    const declarationKind = variableStatement
      ? variableStatement.getDeclarationKind()
      : "const";
    const exportPrefix = variableStatement && variableStatement.isExported() ? "export " : "";
    return `${exportPrefix}${declarationKind} ${symbol.getText()};`;
  }

  return symbol.getFullText();
}

function moveModule(args) {
  const { source, target, projectRoot, dryRun } = args;

  // Validate paths
  validatePath(source, projectRoot);
  validatePath(target, projectRoot);

  const project = getProject(projectRoot);
  const root = path.resolve(projectRoot);
  const allFiles = loadProjectSourceFiles(project, root);

  const sourceFile = project.addSourceFileAtPath(path.join(root, source));
  const sourceFilePath = sourceFile.getFilePath();
  const targetDir = path.dirname(path.join(root, target));
  const targetFilePath = path.join(root, target);

  const moduleReferences = collectModuleReferences(allFiles, sourceFilePath, (refFile) => ({
    file: refFile,
    imports: refFile.getImportDeclarations().filter((imp) => {
      const resolvedFile = imp.getModuleSpecifierSourceFile();
      return resolvedFile && resolvedFile.getFilePath() === sourceFilePath;
    }),
    exports: refFile.getExportDeclarations().filter((exp) => {
      const resolvedFile = exp.getModuleSpecifierSourceFile();
      return resolvedFile && resolvedFile.getFilePath() === sourceFilePath;
    }),
    requires: getRequireCallsReferencingFile(refFile, sourceFilePath),
  }));
  const affectedFiles = [source, ...moduleReferences.map((entry) => path.relative(root, entry.file.getFilePath()))];

  if (!dryRun) {
    // Ensure target directory exists
    if (!fs.existsSync(targetDir)) {
      fs.mkdirSync(targetDir, { recursive: true });
    }

    // Move the file
    sourceFile.move(targetFilePath);

    for (const entry of moduleReferences) {
      const newSpecifier = toModuleSpecifier(entry.file.getFilePath(), targetFilePath);

      for (const imp of entry.imports) {
        imp.setModuleSpecifier(newSpecifier);
      }

      for (const exp of entry.exports) {
        exp.setModuleSpecifier(newSpecifier);
      }

      for (const requireCall of entry.requires) {
        const [firstArg] = requireCall.getArguments();
        if (firstArg) {
          firstArg.replaceWithText(JSON.stringify(newSpecifier));
        }
      }
    }

    project.saveSync();
  }

  return {
    success: true,
    dry_run: dryRun,
    source,
    target,
    affected_files: affectedFiles,
    changes_count: affectedFiles.length,
  };
}

function moveSymbol(args) {
  const { sourceFile: srcPath, symbolName, targetFile: tgtPath, projectRoot, dryRun } = args;

  // Validate paths
  validatePath(srcPath, projectRoot);
  validatePath(tgtPath, projectRoot);

  const project = getProject(projectRoot);
  const root = path.resolve(projectRoot);

  const sourceFile = project.addSourceFileAtPath(path.join(root, srcPath));
  const sourceFilePath = sourceFile.getFilePath();
  let targetFile = project.getSourceFile(path.join(root, tgtPath));

  if (!targetFile) {
    if (dryRun) {
      throw new Error(`Target file does not exist: ${tgtPath}`);
    }
    targetFile = project.createSourceFile(path.join(root, tgtPath), "");
  }

  // Find the symbol
  const symbol =
    sourceFile.getFunction(symbolName) ||
    sourceFile.getClass(symbolName) ||
    sourceFile.getInterface(symbolName) ||
    sourceFile.getTypeAlias(symbolName) ||
    sourceFile.getVariableDeclaration(symbolName);

  if (!symbol) {
    throw new Error(`Symbol '${symbolName}' not found in ${srcPath}`);
  }

  const targetFilePath = targetFile.getFilePath();

  // Find all files that reference this symbol
  const referencingNodes = symbol.findReferencesAsNodes ? symbol.findReferencesAsNodes() : [];
  const referencingFiles = [...new Set(referencingNodes.map((n) => n.getSourceFile()))];
  const affectedFiles = [srcPath, tgtPath, ...referencingFiles
    .filter(f => f.getFilePath() !== sourceFile.getFilePath() && f.getFilePath() !== targetFile.getFilePath())
    .map((f) => path.relative(root, f.getFilePath()))];

  if (!dryRun) {
    const isExported = symbol.isExported ? symbol.isExported() : false;
    const isTypeOnlySymbol = symbol.getKind() === SyntaxKind.InterfaceDeclaration
      || symbol.getKind() === SyntaxKind.TypeAliasDeclaration;
    // Get the full text of the symbol including leading trivia (comments, etc)
    const symbolText = getMovedSymbolText(symbol);
    const sourceSpecifier = toModuleSpecifier(sourceFilePath, targetFilePath);

    targetFile.addStatements(symbolText);

    const newSpecifierByFile = new Map();
    for (const refFile of referencingFiles) {
      if (refFile.getFilePath() === sourceFilePath) continue;

      const shouldAddReplacement = refFile.getFilePath() !== targetFilePath;
      const newSpecifier = newSpecifierByFile.get(refFile.getFilePath())
        || toModuleSpecifier(refFile.getFilePath(), targetFilePath);
      newSpecifierByFile.set(refFile.getFilePath(), newSpecifier);

      const imports = refFile.getImportDeclarations();
      for (const imp of imports) {
        const resolvedFile = imp.getModuleSpecifierSourceFile();
        if (resolvedFile && resolvedFile.getFilePath() === sourceFilePath) {
          const namedImports = imp.getNamedImports();
          const symbolImports = namedImports.filter((namedImport) => namedImport.getName() === symbolName);

          for (const symbolImport of symbolImports) {
            const alias = symbolImport.getAliasNode()?.getText();
            const isTypeOnly = symbolImport.isTypeOnly();
            symbolImport.remove();
            removeEmptyImportDeclaration(imp);

            if (shouldAddReplacement) {
              ensureNamedImport(refFile, newSpecifier, {
                name: symbolName,
                alias,
                isTypeOnly,
              });
            }
          }
        }
      }

      const exports = refFile.getExportDeclarations();
      for (const exp of exports) {
        const resolvedFile = exp.getModuleSpecifierSourceFile();
        if (resolvedFile && resolvedFile.getFilePath() === sourceFilePath) {
          const symbolExports = exp.getNamedExports().filter((namedExport) => namedExport.getName() === symbolName);

          for (const symbolExport of symbolExports) {
            const alias = symbolExport.getAliasNode()?.getText();
            const isTypeOnly = symbolExport.isTypeOnly();
            symbolExport.remove();
            removeEmptyExportDeclaration(exp);

            if (shouldAddReplacement) {
              ensureNamedExport(refFile, newSpecifier, {
                name: symbolName,
                alias,
                isTypeOnly,
              });
            }
          }
        }
      }
    }

    // Remove from source file
    symbol.remove();

    const sourceStillReferencesSymbol = sourceFile
      .getDescendantsOfKind(SyntaxKind.Identifier)
      .some((identifier) => identifier.getText() === symbolName);

    if (sourceStillReferencesSymbol) {
      ensureNamedImport(sourceFile, sourceSpecifier, {
        name: symbolName,
        alias: undefined,
        isTypeOnly: isTypeOnlySymbol,
      });
    }

    if (isExported) {
      ensureNamedExport(sourceFile, sourceSpecifier, {
        name: symbolName,
        alias: undefined,
        isTypeOnly: isTypeOnlySymbol,
      });
    }

    project.saveSync();
  }

  return {
    success: true,
    dry_run: dryRun,
    symbol: symbolName,
    source: srcPath,
    target: tgtPath,
    affected_files: [...new Set(affectedFiles)],
  };
}

function renameSymbol(args) {
  const { file, oldName, newName, projectRoot, dryRun } = args;

  // Validate path
  validatePath(file, projectRoot);

  const project = getProject(projectRoot);
  const root = path.resolve(projectRoot);

  const sourceFile = project.addSourceFileAtPath(path.join(root, file));

  // Find the symbol
  const symbol =
    sourceFile.getFunction(oldName) ||
    sourceFile.getClass(oldName) ||
    sourceFile.getInterface(oldName) ||
    sourceFile.getTypeAlias(oldName) ||
    sourceFile.getVariableDeclaration(oldName);

  if (!symbol) {
    throw new Error(`Symbol '${oldName}' not found in ${file}`);
  }

  // Get all files that reference this symbol
  const referencingNodes = symbol.findReferencesAsNodes ? symbol.findReferencesAsNodes() : [];
  const referencingFiles = [...new Set(referencingNodes.map((n) => n.getSourceFile()))];
  const affectedFiles = [file, ...referencingFiles.map((f) => path.relative(root, f.getFilePath()))];

  if (!dryRun) {
    symbol.rename(newName);
    project.saveSync();
  }

  return {
    success: true,
    dry_run: dryRun,
    old_name: oldName,
    new_name: newName,
    file,
    affected_files: [...new Set(affectedFiles)],
  };
}

function validateImports(args) {
  const { projectRoot } = args;
  const root = path.resolve(projectRoot);

  if (!fs.existsSync(root)) {
    return { errors: [{ error: `Project root does not exist: ${projectRoot}`, type: "invalid_root" }] };
  }

  const project = getProject(projectRoot);
  loadProjectSourceFiles(project, root);

  // Add all TS/JS files
  const errors = [];
  const diagnostics = project.getPreEmitDiagnostics();

  for (const diag of diagnostics) {
    const sourceFile = diag.getSourceFile();
    if (!sourceFile) continue;

    const message = diag.getMessageText();
    const messageText = typeof message === "string" ? message : message.getMessageText();

    if (messageText.includes("Cannot find module") || messageText.includes("has no exported member")) {
      errors.push({
        file: path.relative(root, sourceFile.getFilePath()),
        line: diag.getLineNumber() || 0,
        error: messageText,
        type: "import_error",
      });
    }
  }

  return { errors };
}

// Main
const [operation, argsJson] = process.argv.slice(2);
const args = JSON.parse(argsJson);

const operations = {
  move_module: moveModule,
  move_symbol: moveSymbol,
  rename_symbol: renameSymbol,
  validate_imports: validateImports,
};

if (!operations[operation]) {
  console.error(JSON.stringify({ error: `Unknown operation: ${operation}` }));
  process.exit(1);
}

try {
  const result = operations[operation](args);
  console.log(JSON.stringify(result));
} catch (error) {
  console.error(JSON.stringify({ error: error.message }));
  process.exit(1);
}
