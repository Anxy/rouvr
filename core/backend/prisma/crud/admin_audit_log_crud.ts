import { PrismaClient } from '@prisma/client';
import type { Prisma } from '@prisma/client';

type PrismaConfig = { datasources?: Prisma.Datasources; log?: Prisma.LogDefinition[] };
type PrismaLifecycleHook = () => Promise<void> | void;

let prisma: PrismaClient | null = null;
const shutdownHooks = new Set<PrismaLifecycleHook>();

export function getPrismaClient(
  config: PrismaConfig = {},
  lifecycle: { onShutdown?: PrismaLifecycleHook } = {}
) {
  if (!prisma) {
    prisma = new PrismaClient({
      datasources: config.datasources,
      log: config.log,
    });
  }
  if (lifecycle.onShutdown) {
    shutdownHooks.add(lifecycle.onShutdown);
  }
  return prisma;
}

export async function shutdownPrismaClient() {
  if (!prisma) {
    return;
  }
  const client = prisma;
  prisma = null;
  await client.$disconnect();
  for (const hook of shutdownHooks) {
    await hook();
  }
  shutdownHooks.clear();
}

export async function listAdminAuditLogs(
  args: Prisma.AdminAuditLogFindManyArgs = {}
) {
  return getPrismaClient().adminAuditLog.findMany(args);
}

export async function getAdminAuditLog(
  where: Prisma.AdminAuditLogWhereUniqueInput
) {
  return getPrismaClient().adminAuditLog.findUnique({ where });
}

export async function createAdminAuditLog(
  data: Prisma.AdminAuditLogCreateInput
) {
  return getPrismaClient().adminAuditLog.create({ data });
}

export async function updateAdminAuditLog(
  where: Prisma.AdminAuditLogWhereUniqueInput,
  data: Prisma.AdminAuditLogUpdateInput
) {
  return getPrismaClient().adminAuditLog.update({ where, data });
}

export async function deleteAdminAuditLog(
  where: Prisma.AdminAuditLogWhereUniqueInput
) {
  return getPrismaClient().adminAuditLog.delete({ where });
}

export async function upsertAdminAuditLog(
  where: Prisma.AdminAuditLogWhereUniqueInput,
  create: Prisma.AdminAuditLogCreateInput,
  update: Prisma.AdminAuditLogUpdateInput
) {
  return getPrismaClient().adminAuditLog.upsert({ where, create, update });
}

export type { Prisma };
export type { PrismaConfig, PrismaLifecycleHook };
