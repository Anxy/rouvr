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

export async function listAixGatewayDecisions(
  args: Prisma.AixGatewayDecisionFindManyArgs = {}
) {
  return getPrismaClient().aixGatewayDecision.findMany(args);
}

export async function getAixGatewayDecision(
  where: Prisma.AixGatewayDecisionWhereUniqueInput
) {
  return getPrismaClient().aixGatewayDecision.findUnique({ where });
}

export async function createAixGatewayDecision(
  data: Prisma.AixGatewayDecisionCreateInput
) {
  return getPrismaClient().aixGatewayDecision.create({ data });
}

export async function updateAixGatewayDecision(
  where: Prisma.AixGatewayDecisionWhereUniqueInput,
  data: Prisma.AixGatewayDecisionUpdateInput
) {
  return getPrismaClient().aixGatewayDecision.update({ where, data });
}

export async function deleteAixGatewayDecision(
  where: Prisma.AixGatewayDecisionWhereUniqueInput
) {
  return getPrismaClient().aixGatewayDecision.delete({ where });
}

export async function upsertAixGatewayDecision(
  where: Prisma.AixGatewayDecisionWhereUniqueInput,
  create: Prisma.AixGatewayDecisionCreateInput,
  update: Prisma.AixGatewayDecisionUpdateInput
) {
  return getPrismaClient().aixGatewayDecision.upsert({ where, create, update });
}

export type { Prisma };
export type { PrismaConfig, PrismaLifecycleHook };
