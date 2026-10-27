import { PrismaClient } from '@prisma/client';
const prisma = new PrismaClient();

async function main() {
  console.log('Seeding tables: FeatureFlag');
}

main().finally(async () => {
  await prisma.$disconnect();
});
