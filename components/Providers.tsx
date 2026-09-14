"use client";
import { ConnectionProvider, WalletProvider } from "@solana/wallet-adapter-react";
import { useMemo } from "react";

// Browser reads go through the same-origin relay at /api/rpc unless an RPC URL is set explicitly.
// Transactions are sent by Phantom itself, not through this endpoint.
function rpcEndpoint() {
  if (process.env.NEXT_PUBLIC_RPC_URL) return process.env.NEXT_PUBLIC_RPC_URL;
  if (typeof window !== "undefined") return `${window.location.origin}/api/rpc`;
  return "https://api.mainnet-beta.solana.com";
}

export default function Providers({ children }: { children: React.ReactNode }) {
  const wallets = useMemo(() => [], []); // Phantom is discovered through the Wallet Standard
  const endpoint = useMemo(rpcEndpoint, []);
  return (
    <ConnectionProvider endpoint={endpoint} config={{ commitment: "confirmed" }}>
      <WalletProvider wallets={wallets} autoConnect>{children}</WalletProvider>
    </ConnectionProvider>
  );
}
