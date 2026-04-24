#!/usr/bin/env node
/**
 * TypeScript refactoring operations using ts-morph.
 * Called by the Python backend via subprocess.
 */
const { Node, Project, SyntaxKind, ts } = require("ts-morph");
const fs = require("fs");
const path = require("path");

const SUPPORTED_EXTENSIONS = new Set([".ts", ".tsx", ".js", ".jsx"]);
const SKIP_DIRS = new Set([".git", "dist", "node_modules"]);
const REFACTOR_DIAGNOSTIC_CODES = new Set([2304, 2305, 2306, 2307, 2724]);

function isInsideRoot(candidate, rootRealPath) {
  return candidate === rootRealPath || candidate.startsWith(rootRealPath + path.sep);
}

function resolveExistingParent(candidatePath) {
  let current = candidatePath;
  while (!fs.existsSync(current)) {
    const parent = path.dirname(current);
    if (parent === current) {
      break;
    }
    current = parent;
  }
  return current;
}

function validatePath(filePath, projectRoot) {
  const root = path.resolve(projectRoot);
  if (!fs.existsSync(root)) {
    throw new Error(`Project root does not exist: ${projectRoot}`);
  }

  const rootRealPath = fs.realpathSync(root);
  const resolvedPath = path.resolve(root, filePath);
  const existingParent = resolveExistingParent(resolvedPath);
  const parentRealPath = fs.realpathSync(existingParent);
  const tail = path.relative(existingParent, resolvedPath);
  const realResolvedPath = path.resolve(parentRealPath, tail);
  if (!isInsideRoot(realResolvedPath, rootRealPath)) {
    throw new Error(`Path '${filePath}' escapes project root`);
  }

  return resolvedPath;
}

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

function filterProjectSourceFiles(project, root) {
  return project.getSourceFiles().filter((sourceFile) => {
    const filePath = sourceFile.getFilePath();
    return isInsideRoot(filePath, root);
  });
}

function crawlProjectFiles(root) {
  const rootRealPath = fs.realpathSync(root);
  const files = new Set();
  const warnings = [];
  const visitedDirs = new Set();

  function walk(currentPath) {
    let currentRealPath;
    try {
      currentRealPath = fs.realpathSync(currentPath);
    } catch (error) {
      warnings.push(`Skipped path with unresolved realpath: ${path.relative(root, currentPath)}`);
      return;
    }

    if (!isInsideRoot(currentRealPath, rootRealPath)) {
      warnings.push(`Skipped symlink outside project root: ${path.relative(root, currentPath)}`);
      return;
    }
    if (visitedDirs.has(currentRealPath)) {
      warnings.push(`Skipped recursive symlink: ${path.relative(root, currentPath)}`);
      return;
    }
    visitedDirs.add(currentRealPath);

    for (const entry of fs.readdirSync(currentPath, { withFileTypes: true })) {
      if (SKIP_DIRS.has(entry.name)) {
        continue;
      }

      const entryPath = path.join(currentPath, entry.name);
      const entryLstat = fs.lstatSync(entryPath);
      if (entryLstat.isSymbolicLink()) {
        const realEntryPath = fs.realpathSync(entryPath);
        if (!isInsideRoot(realEntryPath, rootRealPath)) {
          warnings.push(`Skipped symlink outside project root: ${path.relative(root, entryPath)}`);
          continue;
        }

        const stat = fs.statSync(entryPath);
        if (stat.isDirectory()) {
          walk(entryPath);
          continue;
        }

        if (stat.isFile() && SUPPORTED_EXTENSIONS.has(path.extname(entryPath))) {
          files.add(realEntryPath);
        }
        continue;
      }

      if (entry.isDirectory()) {
        walk(entryPath);
        continue;
      }

      if (entry.isFile() && SUPPORTED_EXTENSIONS.has(path.extname(entry.name))) {
        files.add(entryPath);
      }
    }
  }

  walk(root);
  return {
    files: [...files],
    warnings,
  };
}

function loadProjectSourceFiles(project, root, options = {}) {
  if (options.compilerOnly && fs.existsSync(path.join(root, "tsconfig.json"))) {
    return {
      files: filterProjectSourceFiles(project, root),
      warnings: [],
    };
  }

  const crawled = crawlProjectFiles(root);
  for (const filePath of crawled.files) {
    if (!project.getSourceFile(filePath)) {
      project.addSourceFileAtPath(filePath);
    }
  }

  return {
    files: filterProjectSourceFiles(project, root),
    warnings: crawled.warnings,
  };
}

function inferImportSuffix(project, targetFilePath) {
  const compilerOptions = project.getCompilerOptions();
  if (compilerOptions.allowImportingTsExtensions === true) {
    return path.extname(targetFilePath);
  }
  if (compilerOptions.module === ts.ModuleKind.NodeNext || compilerOptions.module === ts.ModuleKind.Node16) {
    return ".js";
  }
  if (compilerOptions.module === undefined) {
    throw new Error("cannot infer import extension style; pass originalSpecifier or configure tsconfig");
  }
  return "";
}

function toModuleSpecifier(fromFilePath, targetFilePath, options = {}) {
  const { originalSpecifier, project } = options;
  let relativePath = path.relative(path.dirname(fromFilePath), targetFilePath);
  if (!relativePath.startsWith(".")) {
    relativePath = "./" + relativePath;
  }
  relativePath = relativePath.replace(/\\/g, "/");
  const bareSpecifier = relativePath.replace(/\.(ts|tsx|js|jsx)$/, "");

  if (originalSpecifier !== undefined) {
    const originalExtension = path.extname(originalSpecifier);
    if (SUPPORTED_EXTENSIONS.has(originalExtension)) {
      return `${bareSpecifier}${originalExtension}`;
    }
    return bareSpecifier;
  }

  return `${bareSpecifier}${inferImportSuffix(project, targetFilePath)}`;
}

function resolveLocalModuleSpecifier(refFilePath, specifier) {
  if (!specifier.startsWith(".")) {
    return null;
  }

  const basePath = path.resolve(path.dirname(refFilePath), specifier);
  const candidates = [
    `${basePath}.ts`,
    `${basePath}.tsx`,
    `${basePath}.js`,
    `${basePath}.jsx`,
    path.join(basePath, "index.ts"),
    path.join(basePath, "index.tsx"),
    path.join(basePath, "index.js"),
    path.join(basePath, "index.jsx"),
    basePath,
  ];

  return candidates.find((candidate) => {
    if (!fs.existsSync(candidate)) {
      return false;
    }
    const stat = fs.statSync(candidate);
    return stat.isFile();
  }) || null;
}

function getRequireCallsReferencingFile(refFile, sourceFilePath) {
  return refFile.getDescendantsOfKind(SyntaxKind.CallExpression).filter((callExpression) => {
    if (callExpression.getExpression().getText() !== "require") {
      return false;
    }

    const [firstArgument] = callExpression.getArguments();
    if (!firstArgument) {
      return false;
    }

    const kind = firstArgument.getKind();
    if (kind !== SyntaxKind.StringLiteral && kind !== SyntaxKind.NoSubstitutionTemplateLiteral) {
      return false;
    }

    const resolvedPath = resolveLocalModuleSpecifier(refFile.getFilePath(), firstArgument.getLiteralValue());
    return resolvedPath === sourceFilePath;
  });
}

function buildNamedSpecifier(name, alias, isTypeOnly) {
  if (!alias && !isTypeOnly) {
    return name;
  }
  return { name, alias, isTypeOnly };
}

function ensureNamedImport(refFile, moduleSpecifier, options) {
  const existingImport = refFile.getImportDeclarations().find((declaration) => {
    return declaration.getModuleSpecifierValue() === moduleSpecifier
      && !declaration.getNamespaceImport()
      && !(declaration.isTypeOnly() && !options.isTypeOnly);
  });

  if (existingImport) {
    const alreadyPresent = existingImport.getNamedImports().some((namedImport) => {
      const alias = namedImport.getAliasNode()?.getText();
      return namedImport.getName() === options.name
        && alias === options.alias
        && namedImport.isTypeOnly() === options.isTypeOnly;
    });

    if (!alreadyPresent) {
      existingImport.addNamedImport(buildNamedSpecifier(options.name, options.alias, options.isTypeOnly));
    }
    return;
  }

  refFile.addImportDeclaration({
    moduleSpecifier,
    namedImports: [buildNamedSpecifier(options.name, options.alias, options.isTypeOnly)],
  });
}

function ensureDefaultImport(refFile, moduleSpecifier, name) {
  const existingImport = refFile.getImportDeclarations().find((declaration) => {
    return declaration.getModuleSpecifierValue() === moduleSpecifier
      && declaration.getDefaultImport() !== undefined;
  });

  if (existingImport) {
    if (existingImport.getDefaultImport().getText() !== name) {
      throw new Error(`Target import collision for default import '${name}' from '${moduleSpecifier}'`);
    }
    return;
  }

  refFile.addImportDeclaration({
    moduleSpecifier,
    defaultImport: name,
  });
}

function ensureNamespaceImport(refFile, moduleSpecifier, name) {
  const existingImport = refFile.getImportDeclarations().find((declaration) => {
    return declaration.getModuleSpecifierValue() === moduleSpecifier
      && declaration.getNamespaceImport() !== undefined;
  });

  if (existingImport) {
    if (existingImport.getNamespaceImport().getText() !== name) {
      throw new Error(`Target import collision for namespace import '${name}' from '${moduleSpecifier}'`);
    }
    return;
  }

  refFile.addImportDeclaration({
    moduleSpecifier,
    namespaceImport: name,
  });
}

function ensureNamedExport(refFile, moduleSpecifier, options) {
  const existingExport = refFile.getExportDeclarations().find((declaration) => {
    return declaration.getModuleSpecifierValue() === moduleSpecifier
      && !(declaration.isTypeOnly() && !options.isTypeOnly);
  });

  if (existingExport) {
    const alreadyPresent = existingExport.getNamedExports().some((namedExport) => {
      const alias = namedExport.getAliasNode()?.getText();
      return namedExport.getName() === options.name
        && alias === options.alias
        && namedExport.isTypeOnly() === options.isTypeOnly;
    });

    if (!alreadyPresent) {
      existingExport.addNamedExport(buildNamedSpecifier(options.name, options.alias, options.isTypeOnly));
    }
    return;
  }

  refFile.addExportDeclaration({
    moduleSpecifier,
    namedExports: [buildNamedSpecifier(options.name, options.alias, options.isTypeOnly)],
  });
}

function ensureDefaultReexport(refFile, moduleSpecifier) {
  const existingExport = refFile.getExportDeclarations().find((declaration) => {
    return declaration.getModuleSpecifierValue() === moduleSpecifier
      && declaration.getNamedExports().some((namedExport) => namedExport.getName() === "default");
  });
  if (existingExport) {
    return;
  }

  refFile.addExportDeclaration({
    moduleSpecifier,
    namedExports: [{ name: "default" }],
  });
}

function removeEmptyImportDeclaration(importDeclaration) {
  if (
    importDeclaration.getNamedImports().length === 0
    && !importDeclaration.getDefaultImport()
    && !importDeclaration.getNamespaceImport()
  ) {
    importDeclaration.remove();
  }
}

function removeEmptyExportDeclaration(exportDeclaration) {
  if (exportDeclaration.getNamedExports().length === 0) {
    exportDeclaration.remove();
  }
}

function getMovedSymbolText(symbol) {
  if (Node.isVariableDeclaration(symbol)) {
    return symbol.getVariableStatement().getFullText();
  }
  return symbol.getFullText();
}

function getLineAndColumn(sourceFile, node) {
  return sourceFile.getLineAndColumnAtPos(node.getStart());
}

function nodeMatchesSelector(node, sourceFile, line, column) {
  if (line === null || line === undefined) {
    return true;
  }
  const position = getLineAndColumn(sourceFile, node);
  if (position.line !== line) {
    return false;
  }
  if (column === null || column === undefined) {
    return true;
  }
  return position.column === column;
}

function uniqueNodes(nodes) {
  const seen = new Set();
  return nodes.filter((node) => {
    const key = `${node.getSourceFile().getFilePath()}:${node.getStart()}`;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

function getTopLevelSymbolCandidates(sourceFile, name) {
  return uniqueNodes([
    ...sourceFile.getFunctions().filter((node) => node.getName() === name),
    ...sourceFile.getClasses().filter((node) => node.getName() === name),
    ...sourceFile.getInterfaces().filter((node) => node.getName() === name),
    ...sourceFile.getTypeAliases().filter((node) => node.getName() === name),
    ...sourceFile.getEnums().filter((node) => node.getName() === name),
    ...sourceFile.getVariableDeclarations().filter((node) => node.getName() === name),
  ]);
}

function getParameterCandidates(sourceFile, name) {
  return uniqueNodes(
    sourceFile.getDescendantsOfKind(SyntaxKind.Parameter).filter((parameter) => {
      const nameNode = parameter.getNameNode();
      return Node.isIdentifier(nameNode) && nameNode.getText() === name;
    })
  );
}

function selectRenameCandidate(sourceFile, oldName, line, column) {
  const candidates = [
    ...getTopLevelSymbolCandidates(sourceFile, oldName),
    ...getParameterCandidates(sourceFile, oldName),
  ];
  const matching = candidates.filter((node) => {
    const nameNode = node.getNameNode ? node.getNameNode() : node;
    return nodeMatchesSelector(nameNode, sourceFile, line, column);
  });

  if (matching.length === 0) {
    throw new Error(`Symbol '${oldName}' not found in ${sourceFile.getBaseName()}`);
  }
  if (line !== null && line !== undefined && matching.length > 1) {
    throw new Error(`Symbol '${oldName}' is ambiguous at line ${line}; pass column`);
  }
  if (line === null || line === undefined) {
    if (matching.length === 1) {
      return matching[0];
    }
    throw new Error(`Symbol '${oldName}' is ambiguous in ${sourceFile.getBaseName()}; pass line and column`);
  }

  return matching[0];
}

function hasTopLevelBinding(sourceFile, name) {
  return sourceFile.getStatements().some((statement) => {
    if (Node.isFunctionDeclaration(statement) || Node.isClassDeclaration(statement)
      || Node.isInterfaceDeclaration(statement) || Node.isTypeAliasDeclaration(statement)
      || Node.isEnumDeclaration(statement)) {
      return statement.getName() === name;
    }

    if (Node.isVariableStatement(statement)) {
      return statement.getDeclarations().some((declaration) => {
        return Node.isIdentifier(declaration.getNameNode()) && declaration.getName() === name;
      });
    }

    if (Node.isImportDeclaration(statement)) {
      if (statement.getDefaultImport()?.getText() === name) {
        return true;
      }
      if (statement.getNamespaceImport()?.getText() === name) {
        return true;
      }
      return statement.getNamedImports().some((namedImport) => {
        return (namedImport.getAliasNode()?.getText() || namedImport.getName()) === name;
      });
    }

    return false;
  });
}

function resolveMovableSymbol(sourceFile, symbolName) {
  const candidates = getTopLevelSymbolCandidates(sourceFile, symbolName);
  if (candidates.length === 0) {
    throw new Error(`Symbol '${symbolName}' not found in ${sourceFile.getBaseName()}`);
  }
  if (candidates.length > 1) {
    throw new Error(`move_symbol cannot handle merged declarations for '${symbolName}'`);
  }

  const symbol = candidates[0];
  if (Node.isVariableDeclaration(symbol)) {
    const declarationList = symbol.getVariableStatement().getDeclarationList();
    if (declarationList.getDeclarations().length > 1) {
      throw new Error(`move_symbol cannot move '${symbolName}' from a multi-declarator statement; split it first`);
    }
    if (!Node.isIdentifier(symbol.getNameNode())) {
      throw new Error(`move_symbol cannot move '${symbolName}' from a destructuring declaration`);
    }
  }

  return symbol;
}

function isNodeWithin(node, container) {
  return node.getStart() >= container.getStart() && node.getEnd() <= container.getEnd();
}

function isReferenceIdentifier(identifier, symbolRoot) {
  if (identifier.getStart() === symbolRoot.getStart()) {
    return false;
  }

  const parent = identifier.getParent();
  if (!parent) {
    return false;
  }

  if (Node.isPropertyAccessExpression(parent) && parent.getNameNode() === identifier) {
    return false;
  }
  if (Node.isPropertyAssignment(parent) && parent.getNameNode() === identifier) {
    return false;
  }
  if (Node.isBindingElement(parent)) {
    return parent.getPropertyNameNode() !== identifier && parent.getNameNode() !== identifier;
  }
  if (
    Node.isImportSpecifier(parent)
    || Node.isExportSpecifier(parent)
    || Node.isImportClause(parent)
    || Node.isNamespaceImport(parent)
    || Node.isNamespaceExport(parent)
    || Node.isNamedImports(parent)
    || Node.isNamedExports(parent)
    || Node.isTypeParameterDeclaration(parent)
  ) {
    return false;
  }
  if (
    (Node.isFunctionDeclaration(parent) || Node.isClassDeclaration(parent) || Node.isInterfaceDeclaration(parent)
      || Node.isTypeAliasDeclaration(parent) || Node.isEnumDeclaration(parent) || Node.isMethodDeclaration(parent)
      || Node.isMethodSignature(parent) || Node.isPropertyDeclaration(parent) || Node.isPropertySignature(parent)
      || Node.isVariableDeclaration(parent) || Node.isParameterDeclaration(parent))
    && parent.getNameNode?.() === identifier
  ) {
    return false;
  }
  if (Node.isQualifiedName(parent) && parent.getRight() === identifier) {
    return false;
  }

  return true;
}

function isTopLevelDeclaration(declaration, sourceFile) {
  if (declaration.getSourceFile() !== sourceFile) {
    return false;
  }
  if (Node.isVariableDeclaration(declaration)) {
    return Node.isSourceFile(declaration.getVariableStatement().getParent());
  }
  return Node.isSourceFile(declaration.getParent());
}

function classifyImportDeclaration(declaration) {
  const importDeclaration = declaration.getFirstAncestorByKind(SyntaxKind.ImportDeclaration);
  if (!importDeclaration) {
    return null;
  }

  const moduleSpecifier = importDeclaration.getModuleSpecifierValue();
  if (Node.isImportSpecifier(declaration)) {
    return {
      kind: "named",
      moduleSpecifier,
      name: declaration.getName(),
      alias: declaration.getAliasNode()?.getText(),
      isTypeOnly: declaration.isTypeOnly() || importDeclaration.isTypeOnly(),
    };
  }
  if (Node.isImportClause(declaration) && declaration.getDefaultImport()) {
    return {
      kind: "default",
      moduleSpecifier,
      name: declaration.getDefaultImport().getText(),
      isTypeOnly: importDeclaration.isTypeOnly(),
    };
  }
  if (Node.isNamespaceImport(declaration)) {
    return {
      kind: "namespace",
      moduleSpecifier,
      name: declaration.getText(),
      isTypeOnly: false,
    };
  }
  return null;
}

function getLocalExportInfo(sourceFile, declaration, localName) {
  if (!isTopLevelDeclaration(declaration, sourceFile)) {
    return { exportShape: "none", localName };
  }

  if (Node.isVariableDeclaration(declaration)) {
    const statement = declaration.getVariableStatement();
    if (statement.getDeclarations().length > 1 || !Node.isIdentifier(declaration.getNameNode())) {
      return { exportShape: "unsupported-var", localName };
    }
    if (statement.isExported()) {
      return { exportShape: statement.isDefaultExport() ? "default" : "named", localName };
    }
  } else if (declaration.isExported?.()) {
    return { exportShape: declaration.isDefaultExport?.() ? "default" : "named", localName };
  }

  for (const exportAssignment of sourceFile.getExportAssignments()) {
    if (!exportAssignment.isExportEquals() && exportAssignment.getExpression().getText() === localName) {
      return { exportShape: "default", localName };
    }
  }

  for (const exportDeclaration of sourceFile.getExportDeclarations()) {
    if (exportDeclaration.getModuleSpecifierValue()) {
      continue;
    }
    for (const namedExport of exportDeclaration.getNamedExports()) {
      if (namedExport.getName() !== localName) {
        continue;
      }
      const alias = namedExport.getAliasNode()?.getText();
      if (alias) {
        return { exportShape: "aliased", localName, externalName: alias };
      }
      return { exportShape: "named", localName };
    }
  }

  return { exportShape: "none", localName };
}

function isTypePosition(identifier) {
  const parent = identifier.getParent();
  if (!parent) {
    return false;
  }
  const parentKind = parent.getKind();
  return Node.isTypeReference(parent)
    || Node.isExpressionWithTypeArguments(parent)
    || Node.isTypeAliasDeclaration(parent)
    || Node.isTypeLiteral(parent)
    || Node.isTypeQuery(parent)
    || Node.isUnionTypeNode(parent)
    || Node.isIntersectionTypeNode(parent)
    || Node.isArrayTypeNode(parent)
    || Node.isTupleTypeNode(parent)
    || Node.isHeritageClause(parent)
    || parentKind === SyntaxKind.TypeOperator;
}

function collectSymbolDependencies(symbol, sourceFile) {
  const passThroughImports = new Map();
  const sourceLocalRefs = new Map();
  const symbolRoot = Node.isVariableDeclaration(symbol) ? symbol.getVariableStatement() : symbol;
  const localDeclarations = uniqueNodes([symbol, ...symbolRoot.getDescendants().filter((node) => {
    if (!node.getSymbol) {
      return false;
    }
    return false;
  })]);

  for (const identifier of symbolRoot.getDescendantsOfKind(SyntaxKind.Identifier)) {
    if (!isReferenceIdentifier(identifier, symbolRoot)) {
      continue;
    }

    const symbolInfo = identifier.getSymbol();
    if (!symbolInfo) {
      continue;
    }

    const declarations = symbolInfo.getDeclarations();
    if (declarations.length === 0) {
      continue;
    }

    if (declarations.every((declaration) => isNodeWithin(declaration, symbolRoot))) {
      continue;
    }

    const importDeclaration = declarations
      .map((declaration) => classifyImportDeclaration(declaration))
      .find(Boolean);
    if (importDeclaration) {
      const key = `${importDeclaration.kind}:${importDeclaration.moduleSpecifier}:${importDeclaration.name}:${importDeclaration.alias || ""}:${importDeclaration.isTypeOnly}`;
      if (!passThroughImports.has(key)) {
        passThroughImports.set(key, importDeclaration);
      }
      continue;
    }

    const localDeclarationsForSymbol = uniqueNodes(
      declarations.filter((declaration) => isTopLevelDeclaration(declaration, sourceFile))
    );
    if (localDeclarationsForSymbol.length === 0) {
      continue;
    }

    const localName = identifier.getText();
    const key = `${localName}:${localDeclarationsForSymbol.map((declaration) => declaration.getStart()).join(",")}`;
    if (!sourceLocalRefs.has(key)) {
      if (localDeclarationsForSymbol.length > 1) {
        sourceLocalRefs.set(key, {
          exportShape: "merged",
          localName,
          declarations: localDeclarationsForSymbol,
          isTypeOnly: isTypePosition(identifier),
        });
        continue;
      }

      const exportInfo = getLocalExportInfo(sourceFile, localDeclarationsForSymbol[0], localName);
      sourceLocalRefs.set(key, {
        ...exportInfo,
        declarations: localDeclarationsForSymbol,
        isTypeOnly: isTypePosition(identifier),
      });
      continue;
    }

    if (!isTypePosition(identifier)) {
      sourceLocalRefs.get(key).isTypeOnly = false;
    }
  }

  return {
    passThroughImports: [...passThroughImports.values()],
    sourceLocalRefs: [...sourceLocalRefs.values()],
  };
}

function ensureDependencyImport(targetFile, moduleSpecifier, dependency) {
  if (dependency.kind === "named") {
    ensureNamedImport(targetFile, moduleSpecifier, {
      name: dependency.name,
      alias: dependency.alias,
      isTypeOnly: dependency.isTypeOnly,
    });
    return;
  }
  if (dependency.kind === "default") {
    ensureDefaultImport(targetFile, moduleSpecifier, dependency.name);
    return;
  }
  if (dependency.kind === "namespace") {
    ensureNamespaceImport(targetFile, moduleSpecifier, dependency.name);
  }
}

function collectModuleReferences(files, sourceFilePath) {
  return files
    .filter((file) => file.getFilePath() !== sourceFilePath)
    .map((file) => ({
      file,
      imports: file.getImportDeclarations()
        .filter((declaration) => {
          const resolvedFile = declaration.getModuleSpecifierSourceFile();
          return resolvedFile && resolvedFile.getFilePath() === sourceFilePath;
        })
        .map((declaration) => ({
          declaration,
          originalSpecifier: declaration.getModuleSpecifierValue(),
        })),
      exports: file.getExportDeclarations()
        .filter((declaration) => {
          const resolvedFile = declaration.getModuleSpecifierSourceFile();
          return resolvedFile && resolvedFile.getFilePath() === sourceFilePath;
        })
        .map((declaration) => ({
          declaration,
          originalSpecifier: declaration.getModuleSpecifierValue(),
        })),
      requires: getRequireCallsReferencingFile(file, sourceFilePath).map((callExpression) => ({
        callExpression,
        originalSpecifier: callExpression.getArguments()[0]?.getLiteralValue(),
      })),
    }))
    .filter((entry) => entry.imports.length > 0 || entry.exports.length > 0 || entry.requires.length > 0);
}

function moveModule(args) {
  const {
    source,
    target,
    projectRoot,
    dryRun,
    overwrite = false,
  } = args;

  const sourcePath = validatePath(source, projectRoot);
  const targetPath = validatePath(target, projectRoot);
  if (sourcePath === targetPath) {
    throw new Error("source and target are identical");
  }
  if (fs.existsSync(targetPath) && targetPath !== sourcePath && !overwrite) {
    throw new Error(`target already exists: ${target}`);
  }

  const project = getProject(projectRoot);
  const root = path.resolve(projectRoot);
  const loaded = loadProjectSourceFiles(project, root);
  const warnings = [...loaded.warnings];

  if (!dryRun && overwrite && fs.existsSync(targetPath) && targetPath !== sourcePath) {
    const existingTarget = project.getSourceFile(targetPath);
    if (existingTarget) {
      existingTarget.deleteImmediatelySync();
    } else {
      fs.unlinkSync(targetPath);
    }
  }

  const projectFiles = filterProjectSourceFiles(project, root);
  const sourceFile = project.getSourceFile(sourcePath) || project.addSourceFileAtPath(sourcePath);
  const sourceFilePath = sourceFile.getFilePath();
  const moduleReferences = collectModuleReferences(projectFiles, sourceFilePath);
  const affectedFiles = [source, target, ...moduleReferences.map((entry) => path.relative(root, entry.file.getFilePath()))];

  if (!dryRun) {
    fs.mkdirSync(path.dirname(targetPath), { recursive: true });
    sourceFile.move(targetPath);

    for (const entry of moduleReferences) {
      for (const { declaration, originalSpecifier } of entry.imports) {
        declaration.setModuleSpecifier(
          toModuleSpecifier(entry.file.getFilePath(), targetPath, {
            originalSpecifier,
            project,
          })
        );
      }

      for (const { declaration, originalSpecifier } of entry.exports) {
        declaration.setModuleSpecifier(
          toModuleSpecifier(entry.file.getFilePath(), targetPath, {
            originalSpecifier,
            project,
          })
        );
      }

      for (const { callExpression, originalSpecifier } of entry.requires) {
        const [firstArgument] = callExpression.getArguments();
        if (!firstArgument) {
          continue;
        }
        firstArgument.replaceWithText(JSON.stringify(
          toModuleSpecifier(entry.file.getFilePath(), targetPath, {
            originalSpecifier,
            project,
          })
        ));
      }
    }

    project.saveSync();
  }

  return {
    success: true,
    dry_run: dryRun,
    source,
    target,
    affected_files: [...new Set(affectedFiles)],
    changes_count: [...new Set(affectedFiles)].length,
    warnings,
    ...(dryRun ? {
      preview: [
        `Move module ${source} to ${target}.`,
        overwrite && fs.existsSync(targetPath) ? "Existing target would be overwritten." : "",
        `Affected files: ${[...new Set(affectedFiles)].join(", ")}`,
      ].filter(Boolean).join("\n"),
    } : {}),
  };
}

function moveSymbol(args) {
  const { sourceFile: srcPath, symbolName, targetFile: tgtPath, projectRoot, dryRun } = args;
  const sourcePath = validatePath(srcPath, projectRoot);
  const targetPath = validatePath(tgtPath, projectRoot);
  if (sourcePath === targetPath) {
    throw new Error("source and target are identical");
  }

  const project = getProject(projectRoot);
  const root = path.resolve(projectRoot);
  const loaded = loadProjectSourceFiles(project, root);
  const warnings = [...loaded.warnings];
  const sourceFile = project.getSourceFile(sourcePath) || project.addSourceFileAtPath(sourcePath);
  let targetFile = project.getSourceFile(targetPath);
  if (!targetFile) {
    if (fs.existsSync(targetPath)) {
      targetFile = project.addSourceFileAtPath(targetPath);
    } else {
      targetFile = project.createSourceFile(targetPath, "", { overwrite: false });
    }
  }

  const symbol = resolveMovableSymbol(sourceFile, symbolName);
  if (hasTopLevelBinding(targetFile, symbolName)) {
    throw new Error(`target ${tgtPath} already has a binding '${symbolName}'`);
  }

  const sourceFilePath = sourceFile.getFilePath();
  const targetFilePath = targetFile.getFilePath();
  const dependencies = collectSymbolDependencies(symbol, sourceFile);
  const mergedDependencies = dependencies.sourceLocalRefs.filter((dependency) => dependency.exportShape === "merged");
  if (mergedDependencies.length > 0) {
    throw new Error(`move_symbol cannot handle merged declarations: ${mergedDependencies.map((dependency) => dependency.localName).join(", ")}`);
  }
  const unsupportedVariables = dependencies.sourceLocalRefs.filter((dependency) => dependency.exportShape === "unsupported-var");
  if (unsupportedVariables.length > 0) {
    throw new Error(`move_symbol cannot promote multi-declarator or destructured variables: ${unsupportedVariables.map((dependency) => dependency.localName).join(", ")}`);
  }
  const unresolvedDependencies = dependencies.sourceLocalRefs.filter((dependency) => dependency.exportShape === "none");
  if (unresolvedDependencies.length > 0) {
    throw new Error(`move_symbol requires these source bindings to be exported first: ${unresolvedDependencies.map((dependency) => dependency.localName).join(", ")}`);
  }

  const isExported = symbol.isExported ? symbol.isExported() : false;
  const isDefaultExport = symbol.isDefaultExport ? symbol.isDefaultExport() : false;
  const isTypeOnlySymbol = Node.isInterfaceDeclaration(symbol) || Node.isTypeAliasDeclaration(symbol);
  const sourceSpecifier = toModuleSpecifier(sourceFilePath, targetFilePath, { project });
  const referencingNodes = symbol.findReferencesAsNodes ? symbol.findReferencesAsNodes() : [];
  const referencingFiles = [...new Set(referencingNodes.map((node) => node.getSourceFile()))];
  const affectedFiles = [
    srcPath,
    tgtPath,
    ...referencingFiles
      .filter((file) => file.getFilePath() !== sourceFilePath && file.getFilePath() !== targetFilePath)
      .map((file) => path.relative(root, file.getFilePath())),
  ];

  if (!dryRun) {
    targetFile.addStatements(getMovedSymbolText(symbol));

    for (const dependency of dependencies.passThroughImports) {
      ensureDependencyImport(targetFile, dependency.moduleSpecifier, dependency);
    }

    for (const dependency of dependencies.sourceLocalRefs) {
      const moduleSpecifier = toModuleSpecifier(targetFilePath, sourceFilePath, { project });
      if (dependency.exportShape === "default") {
        ensureDefaultImport(targetFile, moduleSpecifier, dependency.localName);
        continue;
      }
      if (dependency.exportShape === "aliased") {
        ensureNamedImport(targetFile, moduleSpecifier, {
          name: dependency.externalName,
          alias: dependency.localName,
          isTypeOnly: dependency.isTypeOnly,
        });
        continue;
      }
      ensureNamedImport(targetFile, moduleSpecifier, {
        name: dependency.localName,
        alias: undefined,
        isTypeOnly: dependency.isTypeOnly,
      });
    }

    for (const refFile of referencingFiles) {
      if (refFile.getFilePath() === sourceFilePath || refFile.getFilePath() === targetFilePath) {
        continue;
      }

      for (const importDeclaration of refFile.getImportDeclarations()) {
        const resolvedFile = importDeclaration.getModuleSpecifierSourceFile();
        if (!resolvedFile || resolvedFile.getFilePath() !== sourceFilePath) {
          continue;
        }

        const originalSpecifier = importDeclaration.getModuleSpecifierValue();
        const newSpecifier = toModuleSpecifier(refFile.getFilePath(), targetFilePath, {
          originalSpecifier,
          project,
        });
        const namedImports = importDeclaration.getNamedImports().filter((namedImport) => namedImport.getName() === symbolName);
        for (const namedImport of namedImports) {
          const alias = namedImport.getAliasNode()?.getText();
          const isTypeOnly = namedImport.isTypeOnly();
          namedImport.remove();
          removeEmptyImportDeclaration(importDeclaration);
          ensureNamedImport(refFile, newSpecifier, {
            name: symbolName,
            alias,
            isTypeOnly,
          });
        }
      }

      for (const exportDeclaration of refFile.getExportDeclarations()) {
        const resolvedFile = exportDeclaration.getModuleSpecifierSourceFile();
        if (!resolvedFile || resolvedFile.getFilePath() !== sourceFilePath) {
          continue;
        }

        const originalSpecifier = exportDeclaration.getModuleSpecifierValue();
        const newSpecifier = toModuleSpecifier(refFile.getFilePath(), targetFilePath, {
          originalSpecifier,
          project,
        });
        const namedExports = exportDeclaration.getNamedExports().filter((namedExport) => namedExport.getName() === symbolName);
        for (const namedExport of namedExports) {
          const alias = namedExport.getAliasNode()?.getText();
          const isTypeOnly = namedExport.isTypeOnly();
          namedExport.remove();
          removeEmptyExportDeclaration(exportDeclaration);
          ensureNamedExport(refFile, newSpecifier, {
            name: symbolName,
            alias,
            isTypeOnly,
          });
        }
      }
    }

    symbol.remove();

    const sourceStillReferencesSymbol = sourceFile.getDescendantsOfKind(SyntaxKind.Identifier).some((identifier) => {
      return identifier.getText() === symbolName;
    });

    if (sourceStillReferencesSymbol) {
      ensureNamedImport(sourceFile, sourceSpecifier, {
        name: symbolName,
        alias: undefined,
        isTypeOnly: isTypeOnlySymbol,
      });
    }

    if (isExported) {
      if (isDefaultExport) {
        ensureDefaultReexport(sourceFile, sourceSpecifier);
      } else {
        ensureNamedExport(sourceFile, sourceSpecifier, {
          name: symbolName,
          alias: undefined,
          isTypeOnly: isTypeOnlySymbol,
        });
      }
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
    warnings,
    ...(dryRun ? {
      preview: [
        `Move symbol ${symbolName} from ${srcPath} to ${tgtPath}.`,
        `Affected files: ${[...new Set(affectedFiles)].join(", ")}`,
      ].join("\n"),
    } : {}),
  };
}

function renameSymbol(args) {
  const { file, oldName, newName, projectRoot, dryRun, line = null, column = null } = args;
  const sourcePath = validatePath(file, projectRoot);
  const project = getProject(projectRoot);
  const root = path.resolve(projectRoot);
  const loaded = loadProjectSourceFiles(project, root);
  const warnings = [...loaded.warnings];
  const sourceFile = project.getSourceFile(sourcePath) || project.addSourceFileAtPath(sourcePath);
  const symbol = selectRenameCandidate(sourceFile, oldName, line, column);

  const referencingNodes = symbol.findReferencesAsNodes ? symbol.findReferencesAsNodes() : [];
  const referencingFiles = [...new Set(referencingNodes.map((node) => node.getSourceFile()))];
  const affectedFiles = [file, ...referencingFiles.map((source) => path.relative(root, source.getFilePath()))];

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
    warnings,
    ...(dryRun ? {
      preview: [
        `Rename ${oldName} to ${newName} in ${file}.`,
        `Affected files: ${[...new Set(affectedFiles)].join(", ")}`,
      ].join("\n"),
    } : {}),
  };
}

function validateImports(args) {
  const { projectRoot } = args;
  const root = path.resolve(projectRoot);
  if (!fs.existsSync(root)) {
    return { errors: [{ error: `Project root does not exist: ${projectRoot}`, type: "invalid_root" }] };
  }

  const project = getProject(projectRoot);
  loadProjectSourceFiles(project, root, { compilerOnly: true });
  const errors = [];
  const diagnostics = project.getPreEmitDiagnostics();

  for (const diagnostic of diagnostics) {
    const sourceFile = diagnostic.getSourceFile();
    if (!sourceFile) {
      continue;
    }

    const code = diagnostic.getCode();
    if (!REFACTOR_DIAGNOSTIC_CODES.has(code)) {
      continue;
    }

    const message = diagnostic.getMessageText();
    const messageText = typeof message === "string" ? message : message.getMessageText();
    errors.push({
      file: path.relative(root, sourceFile.getFilePath()),
      line: diagnostic.getLineNumber() || 0,
      error: messageText,
      type: "import_error",
      code,
    });
  }

  return { errors };
}

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
