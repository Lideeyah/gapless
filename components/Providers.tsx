"use client";
import { ConnectionProvider, WalletProvider } from "@solana/wallet-adapter-react";
import { useMemo } from "react";

export const RPC = process.env.NEXT_PUBLIC_RPC_URL ?? "https://api.mainnet-beta.solana.com";

export default function Providers({ children }: { children: React.ReactNode }) {
  const wallets = useMemo(() => [], []); // Phantom is discovered through the Wallet Standard
  return (
    <ConnectionProvider endpoint={RPC} config={{ commitment: "confirmed" }}>
      <WalletProvider wallets={wallets} autoConnect>{children}</WalletProvider>
    </ConnectionProvider>
  );
}
